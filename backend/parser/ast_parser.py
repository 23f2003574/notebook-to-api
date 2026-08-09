import ast


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
                        arg_info["default"] = ast.unparse(
                            default_node
                        )

                args.append(arg_info)

            return_type = None
            if node.returns:
                return_type = ast.unparse(node.returns)

            function_info = {
                "name": node.name,
                "args": args,
                "return_type": return_type,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "example_payload": generate_example_payload(args),
                "example_response": generate_example_response(
                    return_type
                )
            }

            functions.append(function_info)

    return functions


def normalize_type_annotation(arg_type):
    if not arg_type:
        return arg_type

    if arg_type.startswith("Annotated["):
        return (
            arg_type
            .replace("Annotated[", "")
            .rstrip("]")
            .split(",")[0]
            .strip()
        )

    if arg_type.startswith("Optional["):
        return (
            arg_type
            .replace("Optional[", "")
            .replace("]", "")
        )

    if arg_type.startswith("Union["):
        return (
            arg_type
            .replace("Union[", "")
            .replace("]", "")
            .split(",")[0]
            .strip()
        )

    if "|" in arg_type:
        return arg_type.split("|")[0].strip()

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

    # Map common Python import modules to their PyPI package names
    pypi_mapping = {
        "sklearn": "scikit-learn",
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "yaml": "PyYAML",
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