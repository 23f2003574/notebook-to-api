import ast
import re


# Matches a Google-style docstring section header introducing per-
# parameter documentation -- see _parse_docstring_arg_descriptions below
# for what this is for. "Parameters:" (with the trailing colon, unlike
# NumPy's own underlined "Parameters\n----------" style, which this does
# not attempt to parse) is accepted as a common variant seen in the wild
# alongside Google's own "Args:"/"Arguments:".
_ARG_SECTION_HEADER_PATTERN = re.compile(r"^(Args|Arguments|Parameters):$")

# Matches one entry's own "name: description" or "name (type): description"
# opening line within an Args:-style section, e.g. "x: The input value."
# or "epochs (int): Number of training passes, defaults to 10." -- the
# optional "(type)" is accepted but never used (the notebook function's
# own real annotation, not free-text repeated in a docstring, is always
# authoritative for the generated field's actual type).
_ARG_ENTRY_PATTERN = re.compile(r"^([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.*)$")


def _parse_docstring_arg_descriptions(docstring):
    """{parameter_name: description} for every parameter documented in
    `docstring`'s own Google-style "Args:"/"Arguments:"/"Parameters:"
    section, or {} if `docstring` is empty or has no such section.

    Before this, a notebook author's own per-parameter documentation --
    already sitting right there in the function's docstring, exactly
    where a human reading the notebook already looks -- was completely
    discarded: extract_functions_from_code only ever kept the docstring
    as one opaque whole-function blob (used for the endpoint's own
    OpenAPI "description"), with nothing splitting out which sentence
    described which parameter. generate_fastapi_code (api_generator.py)
    had no choice but to fall back to a generic "Parameter 'x' of type
    T" for every single field's own description, no matter how
    thoroughly the notebook author had actually documented it.

    Only Google-style is parsed (a single "Args:"-style header followed
    by indented "name: description" entries) -- NumPy's underlined
    "Parameters\\n----------" style and Sphinx's ":param x:" style are
    deliberately not handled here, to keep this to one well-tested
    convention rather than three partially-supported ones. A docstring
    using either of those simply yields {} here, exactly as if it had no
    per-parameter documentation at all -- the same graceful "nothing to
    extract" fallback a docstring with no Args:-style section at all
    already gets.

    A description spanning multiple lines (a long sentence a human
    wrapped across lines, each indented further than its own "name:"
    line) is joined back into one description with single spaces, the
    same normalization ast.get_docstring(clean=True) already applies to
    the docstring as a whole.
    """
    if not docstring:
        return {}

    lines = docstring.splitlines()
    descriptions = {}

    in_section = False
    section_indent = None
    current_name = None
    current_parts = []

    def flush():
        if current_name is not None:
            text = " ".join(part for part in current_parts if part).strip()
            if text:
                descriptions[current_name] = text

    for line in lines:
        stripped = line.strip()

        if not in_section:
            if _ARG_SECTION_HEADER_PATTERN.match(stripped):
                in_section = True
                section_indent = None
            continue

        if not stripped:
            # A blank line inside the section -- could just be spacing
            # between entries (common when each is more than one
            # sentence), so it doesn't end the section on its own; only
            # an actual dedent (another section header, or the docstring
            # simply ending back at a shallower indent) does that below.
            continue

        indent = len(line) - len(line.lstrip())

        if section_indent is None:
            section_indent = indent
        elif indent < section_indent:
            # Dedented back out of the Args:-style section entirely --
            # e.g. a "Returns:" header at the same level "Args:" itself
            # started at.
            break

        match = _ARG_ENTRY_PATTERN.match(stripped)

        if indent == section_indent and match:
            flush()
            current_name = match.group(1)
            current_parts = [match.group(2)]
        elif current_name is not None:
            # A continuation line, indented further than this entry's
            # own "name:" line -- part of the same parameter's
            # description, wrapped onto another line.
            current_parts.append(stripped)

    flush()

    return descriptions


def deduplicate_functions_by_name(functions):
    """Collapse repeated function definitions, keeping the last one.

    Notebooks are edited iteratively: a cell defining `def add(...)` is
    commonly re-run later with a fixed/changed body under the same name.
    If every extracted definition were kept, the generated FastAPI app
    would register multiple routes for the identical path/method pair.
    Route matching resolves to whichever was registered *first*, while the
    OpenAPI schema (a dict keyed by path) reflects whichever was
    registered *last* -- so the served behaviour and the documented
    behaviour would silently diverge. Keeping only the last definition per
    name matches what actually happens if the whole notebook were executed
    top to bottom in a single kernel: the later `def` always wins.
    """
    deduped = {}

    for func in functions:
        deduped[func["name"]] = func

    return list(deduped.values())


def is_parseable_python(code):
    """Return True if `code` is syntactically valid Python.

    Used to drop cells whose content is still not valid Python after magic
    stripping (e.g. the body of a `%%bash` cell magic) before they are
    written into the generated runtime module, rather than shipping a
    module that fails to import.
    """
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


# Node types that introduce a new Python scope. A function defined inside
# one of these (a class method, or a function nested inside another
# function) is not callable as a free-standing module-level function, so it
# must not be walked into when looking for API-exposable functions.
_SCOPE_BOUNDARY_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

# Compound-statement fields that do NOT introduce a new scope (if/try/for/
# while/with at module level), so definitions inside them are still
# reachable as module-level functions and should be walked into.
_TRANSPARENT_BODY_FIELDS = ("body", "orelse", "finalbody")


def _iter_module_level_statements(nodes):
    """Yield statements reachable at module scope, without descending into
    function/class bodies (which define their own, unrelated scope)."""

    for node in nodes:
        yield node

        if isinstance(node, _SCOPE_BOUNDARY_NODES):
            continue

        for field in _TRANSPARENT_BODY_FIELDS:
            children = getattr(node, field, None)

            if children:
                yield from _iter_module_level_statements(children)

        for handler in getattr(node, "handlers", []):
            yield from _iter_module_level_statements(handler.body)


def extract_functions_from_code(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # A cell can still contain unparseable content after magic-command
        # stripping (e.g. the body of a `%%bash` cell magic). Skip it rather
        # than failing the whole notebook compilation over one cell.
        return []

    functions = []

    for node in _iter_module_level_statements(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A `*args`/`**kwargs` catch-all can't be represented as a
            # fixed set of Pydantic request fields -- the generated
            # endpoint would silently ignore whatever callers actually put
            # there. Skip the whole function (same policy already applied
            # to class methods/nested functions) rather than generating an
            # endpoint that quietly drops part of its own signature.
            if node.args.vararg or node.args.kwarg:
                continue

            args = []

            # Positional-only params (those before a bare `/`, e.g.
            # `def f(a, b, /, c)`) are extracted alongside regular
            # positional params: both are passed positionally in the
            # generated notebook_module.func(...) call, in the same
            # left-to-right order they appear in `positional_params`, so
            # merging them here preserves correct call ordering. Defaults
            # apply to the trailing N of this *combined* list, exactly as
            # for node.args.args alone.
            positional_params = node.args.posonlyargs + node.args.args

            defaults = node.args.defaults

            default_offset = (
                len(positional_params)
                - len(defaults)
            )

            for idx, arg in enumerate(positional_params):
                arg_info = {
                    "name": arg.arg,
                    "type": None,
                    "default": None,
                    "default_is_literal": True,
                    "has_default": False,
                    "kind": "positional"
                }

                if arg.annotation:
                    arg_info["type"] = ast.unparse(arg.annotation)

                default_index = idx - default_offset

                if default_index >= 0:
                    arg_info["has_default"] = True

                    try:
                        arg_info["default"] = ast.literal_eval(
                            defaults[default_index]
                        )
                    except Exception:
                        # Not a literal (e.g. a notebook-defined Enum
                        # member like `Priority.HIGH`, or any other
                        # expression) -- "default" holds its raw source
                        # instead, and default_is_literal=False tells the
                        # generator (see api_generator.py) to embed it as
                        # a qualified code expression rather than
                        # repr()-ing it into a Pydantic Field default,
                        # which would silently turn it into the *string*
                        # "Priority.HIGH" instead of the actual enum
                        # member.
                        arg_info["default_is_literal"] = False
                        arg_info["default"] = ast.unparse(
                            defaults[default_index]
                        )

                args.append(arg_info)

            # Keyword-only args (those after a bare `*` or `*args`), e.g.
            # `def train(data, *, epochs=10, lr=0.01)`. These live in a
            # separate ast.arguments field and are paired positionally with
            # kw_defaults, where a `None` entry means "no default" (the arg
            # is required) rather than "default value None".
            for idx, arg in enumerate(node.args.kwonlyargs):
                arg_info = {
                    "name": arg.arg,
                    "type": None,
                    "default": None,
                    "default_is_literal": True,
                    "has_default": False,
                    "kind": "keyword_only"
                }

                if arg.annotation:
                    arg_info["type"] = ast.unparse(arg.annotation)

                default_node = node.args.kw_defaults[idx]

                if default_node is not None:
                    arg_info["has_default"] = True

                    try:
                        arg_info["default"] = ast.literal_eval(
                            default_node
                        )
                    except Exception:
                        # See the identical positional-arg branch above.
                        arg_info["default_is_literal"] = False
                        arg_info["default"] = ast.unparse(
                            default_node
                        )

                args.append(arg_info)

            return_type = None
            if node.returns:
                return_type = ast.unparse(node.returns)

            # ast.get_docstring(clean=True) strips the docstring's own
            # leading/trailing whitespace and dedents it (equivalent to
            # inspect.cleandoc), the same normalization a caller would
            # expect from reading it any other way. None when the function
            # has no docstring at all, distinct from an empty string, so
            # the generator (see api_generator.py) can tell "nothing to
            # show" apart from "author wrote an empty docstring" and fall
            # back to its own auto-generated description in both cases via
            # a single falsy check.
            docstring = ast.get_docstring(node, clean=True)

            # Attaches each parameter's own Google-style "Args:"
            # description (see _parse_docstring_arg_descriptions above),
            # if the docstring documents it -- generate_fastapi_code
            # (api_generator.py) prefers this over its own generic
            # "Parameter 'x' of type T" fallback whenever present. None
            # (not simply omitted) for a parameter the docstring doesn't
            # mention, the same "distinct from an empty string/absent"
            # convention `docstring` above already follows, so the
            # generator can tell "author didn't document this one"
            # apart from "documented, but with genuinely empty text"
            # (which _parse_docstring_arg_descriptions already never
            # produces -- an empty description is dropped, not kept as
            # "") via a single falsy check either way.
            arg_descriptions = _parse_docstring_arg_descriptions(docstring)

            for arg_info in args:
                arg_info["description"] = arg_descriptions.get(
                    arg_info["name"]
                )

            function_info = {
                "name": node.name,
                "args": args,
                "return_type": return_type,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "docstring": docstring,
                "example_payload": generate_example_payload(args),
                "example_response": generate_example_response(
                    return_type
                )
            }

            functions.append(function_info)

    return functions


def extract_skipped_functions_from_code(code):
    """Function definitions in `code` that extract_functions_from_code
    silently drops, paired with why: either a `*args`/`**kwargs` catch-all
    (module-level, but not representable as a fixed set of Pydantic
    request fields -- see extract_functions_from_code above), or a def
    nested inside a class or another function (not reachable as a
    standalone, callable module-level function at all).

    Before this, a notebook author whose function didn't turn into an
    endpoint had no way to find out why short of reading this parser's own
    source: `inspect`/POST /api/inspect reported every module-level
    function that *did* survive extraction, but never mentioned the ones
    that didn't -- a `**kwargs`-taking function or a class method just
    silently had no corresponding route, with nothing in the compiled
    output or its preview to explain the gap.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    module_level_ids = {
        id(node)
        for node in _iter_module_level_statements(tree.body)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    skipped = []

    for node in ast.walk(tree):

        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        if id(node) in module_level_ids:

            if node.args.vararg or node.args.kwarg:
                skipped.append({
                    "name": node.name,
                    "reason": (
                        "uses *args/**kwargs, which can't be represented "
                        "as a fixed set of request fields"
                    ),
                })

            continue

        skipped.append({
            "name": node.name,
            "reason": (
                "defined inside a class or nested function, so it isn't "
                "callable as a standalone endpoint"
            ),
        })

    return skipped


def _matching_bracket_content(text):
    """`text` is everything after a generic wrapper's opening "[" (e.g.
    the "List[int]]" left over from stripping "Optional[" off the front
    of "Optional[List[int]]"). Returns the content up to (not including)
    the "]" that actually matches that opening bracket, tracking bracket
    depth so a nested generic inside it (the "List[int]" here) isn't
    corrupted by also consuming *its own* closing bracket.

    Without this, a blind ".replace(']', '')" -- what this used to do --
    strips every closing bracket in the whole string, not just the one
    belonging to the wrapper being peeled: "Optional[List[int]]" fell
    apart into the mismatched "List[int" instead of "List[int]", which
    then matched none of the List[/Dict[/Tuple[/Set[ checks below (nor
    anything in the type_defaults maps in generate_example_payload/
    generate_example_response), silently producing a `None` example for
    a field that's actually a list.
    """
    depth = 1

    for i, ch in enumerate(text):

        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1

            if depth == 0:
                return text[:i]

    # Unbalanced input shouldn't happen for a real ast.unparse'd
    # annotation -- fall back to the whole remainder rather than raising.
    return text


def _first_top_level_segment(text, separator):
    """The portion of `text` up to (not including) the first occurrence
    of `separator` that isn't nested inside a "[...]" pair, e.g. for
    "List[int], str" with separator "," this returns "List[int]" rather
    than splitting inside List's own brackets. Returns all of `text`
    unchanged if `separator` never occurs at bracket depth 0.
    """
    depth = 0

    for i, ch in enumerate(text):

        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == separator and depth == 0:
            return text[:i]

    return text


def normalize_type_annotation(arg_type):
    if not arg_type:
        return arg_type

    arg_type = arg_type.strip()

    if arg_type.startswith("Annotated["):
        inner = _matching_bracket_content(arg_type[len("Annotated["):])
        first_arg = _first_top_level_segment(inner, ",").strip()
        return normalize_type_annotation(first_arg)

    if arg_type.startswith("Optional["):
        inner = _matching_bracket_content(arg_type[len("Optional["):])
        return normalize_type_annotation(inner.strip())

    if arg_type.startswith("Union["):
        inner = _matching_bracket_content(arg_type[len("Union["):])
        first_arg = _first_top_level_segment(inner, ",").strip()
        return normalize_type_annotation(first_arg)

    if "|" in arg_type:
        # A top-level PEP 604 union (e.g. "int | str" or "List[int] |
        # None") must be split before recursing -- but a "|" nested
        # inside a generic's own arguments (e.g. "Dict[str, int | float]")
        # is not a top-level union at all and must be left alone here, or
        # this would incorrectly try to normalize the truncated
        # "Dict[str, int " instead of falling through to the Dict[ check
        # below.
        first_arg = _first_top_level_segment(arg_type, "|").strip()

        if first_arg != arg_type:
            return normalize_type_annotation(first_arg)

    if (
        arg_type.startswith("List[")
        or arg_type.startswith("list[")
    ):
        return "list"

    if (
        arg_type.startswith("Dict[")
        or arg_type.startswith("dict[")
    ):
        return "dict"

    if (
        arg_type.startswith("Tuple[")
        or arg_type.startswith("tuple[")
    ):
        return "tuple"

    if (
        arg_type.startswith("Set[")
        or arg_type.startswith("set[")
    ):
        return "set"

    return arg_type


def generate_example_response(return_type):
    if not return_type:
        return {
            "result": None
        }

    return_type = normalize_type_annotation(
        return_type
    )

    if return_type and return_type.startswith("Literal["):
        literal_values = (
            return_type
            .replace("Literal[", "")
            .rstrip("]")
            .split(",")
        )

        first_value = literal_values[0].strip()

        if (
            first_value.startswith('"')
            and first_value.endswith('"')
        ):
            first_value = first_value[1:-1]

        elif (
            first_value.startswith("'")
            and first_value.endswith("'")
        ):
            first_value = first_value[1:-1]

        return {
            "result": first_value
        }

    type_defaults = {
        "int": 0,
        "float": 0.0,
        "str": "",
        "bool": False,
        "list": [],
        "dict": {},
        "tuple": [],
        "set": []
    }

    if return_type in (
        "pd.DataFrame",
        "DataFrame",
        "pd.Series",
        "Series",
        "np.ndarray",
        "ndarray"
    ):
        return {
            "result": []
        }

    return {
        "result": type_defaults.get(
            return_type,
            None
        )
    }


def generate_example_payload(args):
    payload = {}

    type_defaults = {
        "int": 0,
        "float": 0.0,
        "str": "",
        "bool": False,
        "list": [],
        "dict": {},
        "tuple": [],
        "set": []
    }

    for arg in args:
        arg_name = arg.get("name")
        arg_type = normalize_type_annotation(
            arg.get("type")
        )

        if arg_type and arg_type.startswith("Literal["):
            literal_values = (
                arg_type
                .replace("Literal[", "")
                .rstrip("]")
                .split(",")
            )
            first_value = literal_values[0].strip()
            if (
                first_value.startswith('"')
                and first_value.endswith('"')
            ):
                first_value = first_value[1:-1]
            elif (
                first_value.startswith("'")
                and first_value.endswith("'")
            ):
                first_value = first_value[1:-1]
            payload[arg_name] = first_value
            continue

        if arg_type in (
            "pd.DataFrame",
            "DataFrame"
        ):
            payload[arg_name] = []
            continue

        if arg_type in (
            "np.ndarray",
            "ndarray"
        ):
            payload[arg_name] = []
            continue

        if arg_type in (
            "pd.Series",
            "Series"
        ):
            payload[arg_name] = []
            continue

        if arg.get("default") is not None:
            payload[arg_name] = arg["default"]
        else:
            payload[arg_name] = type_defaults.get(
                arg_type,
                None
            )

    return payload


def extract_imports_from_code(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    imports = set()

    # Map common Python import modules to their PyPI package names.
    # Beyond sklearn/cv2/PIL/yaml simply being non-obvious (their import
    # name doesn't match the installable package name at all), several of
    # these are actively dangerous to leave unmapped, the same way an
    # unmapped stdlib name like "asyncio" was before STANDARD_LIBS grew
    # to cover it: PyPI hosts a real, unrelated, unofficial package under
    # the bare import name itself, so `pip install <import name>`
    # silently installs the *wrong* package instead of failing loudly --
    # confirmed real, well-documented traps for "dotenv" (python-dotenv's
    # import name), "jwt" (PyJWT's), "serial" (pyserial's), and "docx"
    # (python-docx's), each with its own long history of developers
    # reporting `ModuleNotFoundError`/broken behavior after installing
    # the wrong same-named package by mistake.
    pypi_mapping = {
        "sklearn": "scikit-learn",
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "yaml": "PyYAML",
        "dotenv": "python-dotenv",
        "jwt": "PyJWT",
        "serial": "pyserial",
        "docx": "python-docx",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base_module = alias.name.split(".")[0]
                imports.add(pypi_mapping.get(base_module, base_module))

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base_module = node.module.split(".")[0]
                imports.add(pypi_mapping.get(base_module, base_module))

    return imports


if __name__ == "__main__":
    sample_code = """
from typing import Optional, Union, Literal, Annotated

def get_name() -> Optional[str]:
    return "alice"

def get_model() -> Literal["xgboost", "rf"]:
    return "xgboost"

def get_user_id() -> int | str:
    return 1

def get_score() -> Union[int, float]:
    return 0
"""

    extracted = extract_functions_from_code(sample_code)
    for func in extracted:
        print("Function:", func)

    imports = extract_imports_from_code(sample_code)
    print("Imports:", imports)