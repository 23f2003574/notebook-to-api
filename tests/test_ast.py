from backend.parser.ast_parser import (
    extract_functions_from_code,
    extract_imports_from_code,
    extract_skipped_functions_from_code,
    deduplicate_functions_by_name,
    generate_example_payload,
    generate_example_response,
    normalize_type_annotation,
    _parse_docstring_arg_descriptions,
)


def test_function_extraction():

    code = """
def add(a: int, b: int) -> int:
    return a + b
"""

    funcs = extract_functions_from_code(code)

    assert funcs[0]["name"] == "add"


def test_argument_extraction():

    code = """
def greet(name: str):
    return name
"""

    funcs = extract_functions_from_code(code)

    assert funcs[0]["args"][0]["name"] == "name"


def test_return_type_extraction():

    code = """
def square(x: int) -> int:
    return x * x
"""

    funcs = extract_functions_from_code(code)

    assert funcs[0]["return_type"] == "int"


def test_function_extraction_captures_the_docstring():

    code = '''
def train_model(epochs: int) -> str:
    """Train the classifier for the given number of epochs.

    Returns a short summary of the final accuracy.
    """
    return "done"
'''

    funcs = extract_functions_from_code(code)

    assert funcs[0]["docstring"] == (
        "Train the classifier for the given number of epochs.\n\n"
        "Returns a short summary of the final accuracy."
    )


def test_function_extraction_docstring_is_none_when_absent():

    code = """
def add(a: int, b: int) -> int:
    return a + b
"""

    funcs = extract_functions_from_code(code)

    assert funcs[0]["docstring"] is None


def test_function_extraction_docstring_is_none_when_blank():
    """A docstring that's only whitespace carries no information a real
    one wouldn't already have -- ast.get_docstring(clean=True) already
    normalizes it down to an empty string, which must come through as the
    same falsy "nothing to show" value as no docstring at all, so the
    generator (api_generator.py) can fall back to its auto-generated
    description with a single truthiness check rather than also handling
    an empty-but-not-None case.
    """

    code = '''
def add(a: int, b: int) -> int:
    """   """
    return a + b
'''

    funcs = extract_functions_from_code(code)

    assert not funcs[0]["docstring"]


def test_function_extraction_attaches_docstring_arg_descriptions():
    """Confirmed missing before this feature: a notebook author's own
    per-parameter documentation, sitting right there in the docstring,
    was completely discarded -- generate_fastapi_code (api_generator.py)
    had no choice but to fall back to a generic "Parameter 'x' of type
    T" for every field regardless of how thoroughly it was documented.
    """

    code = '''
def train(epochs: int, lr: float) -> str:
    """Train the model.

    Args:
        epochs: Number of training passes over the dataset.
        lr: Learning rate for the optimizer.

    Returns:
        A short summary string.
    """
    return "done"
'''

    funcs = extract_functions_from_code(code)
    args_by_name = {arg["name"]: arg for arg in funcs[0]["args"]}

    assert args_by_name["epochs"]["description"] == (
        "Number of training passes over the dataset."
    )
    assert args_by_name["lr"]["description"] == (
        "Learning rate for the optimizer."
    )


def test_function_extraction_arg_description_is_none_when_undocumented():

    code = '''
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''

    funcs = extract_functions_from_code(code)

    assert funcs[0]["args"][0]["description"] is None
    assert funcs[0]["args"][1]["description"] is None


def test_parse_docstring_arg_descriptions_returns_empty_dict_for_no_docstring():

    assert _parse_docstring_arg_descriptions(None) == {}
    assert _parse_docstring_arg_descriptions("") == {}


def test_parse_docstring_arg_descriptions_returns_empty_dict_with_no_args_section():

    docstring = "Just a summary, no Args: section at all."

    assert _parse_docstring_arg_descriptions(docstring) == {}


def test_parse_docstring_arg_descriptions_handles_arguments_and_parameters_headers():
    """"Args:" is the most common Google-style header, but "Arguments:"
    and "Parameters:" are common variants seen in the wild too.
    """

    for header in ("Args:", "Arguments:", "Parameters:"):

        docstring = f"Summary.\n\n{header}\n    x: The value.\n"

        assert _parse_docstring_arg_descriptions(docstring) == {
            "x": "The value."
        }


def test_parse_docstring_arg_descriptions_handles_type_annotation_in_entry():
    """A "name (type): description" entry -- the type is documented for a
    human but never used (the function's own real annotation is always
    authoritative for the generated field's actual type).
    """

    docstring = "Summary.\n\nArgs:\n    epochs (int): Number of passes.\n"

    assert _parse_docstring_arg_descriptions(docstring) == {
        "epochs": "Number of passes."
    }


def test_parse_docstring_arg_descriptions_joins_wrapped_continuation_lines():

    docstring = (
        "Summary.\n\n"
        "Args:\n"
        "    x: A long description that a human\n"
        "        wrapped onto a second line.\n"
    )

    assert _parse_docstring_arg_descriptions(docstring) == {
        "x": "A long description that a human wrapped onto a second line."
    }


def test_parse_docstring_arg_descriptions_stops_at_the_next_section():
    """A "Returns:" section (or any other dedented header) must not be
    mistaken for another documented parameter.
    """

    docstring = (
        "Summary.\n\n"
        "Args:\n"
        "    x: The input.\n\n"
        "Returns:\n"
        "    The output.\n"
    )

    assert _parse_docstring_arg_descriptions(docstring) == {"x": "The input."}


def test_parse_docstring_arg_descriptions_ignores_numpy_style():
    """Only Google-style is parsed -- a NumPy-style underlined
    "Parameters\\n----------" section has no "Args:"/"Arguments:"/
    "Parameters:" header line at all, so this must yield {} rather than
    a wrong or partial extraction.
    """

    docstring = (
        "Summary.\n\n"
        "Parameters\n"
        "----------\n"
        "x : int\n"
        "    The input.\n"
    )

    assert _parse_docstring_arg_descriptions(docstring) == {}


def test_function_extraction_skips_unparseable_code_instead_of_raising():

    code = "%%bash\necho hi"

    funcs = extract_functions_from_code(code)

    assert funcs == []


def test_import_extraction_skips_unparseable_code_instead_of_raising():

    code = "%%bash\necho hi"

    imports = extract_imports_from_code(code)

    assert imports == set()


def test_function_extraction_excludes_class_methods():

    code = """
class Model:
    def predict(self, x: int) -> int:
        return x * 2
"""

    funcs = extract_functions_from_code(code)

    assert [f["name"] for f in funcs] == []


def test_function_extraction_excludes_nested_functions():

    code = """
def outer(a: int) -> int:
    def inner(b: int) -> int:
        return b + 1
    return inner(a)
"""

    funcs = extract_functions_from_code(code)

    assert [f["name"] for f in funcs] == ["outer"]


def test_function_extraction_includes_module_level_function_beside_a_class():

    code = """
class Model:
    def predict(self, x: int) -> int:
        return x * 2

def run(x: int) -> int:
    model = Model()
    return model.predict(x)
"""

    funcs = extract_functions_from_code(code)

    assert [f["name"] for f in funcs] == ["run"]


def test_function_extraction_includes_functions_defined_inside_if_block():

    code = """
if True:
    def conditional_func(y: int) -> int:
        return y
"""

    funcs = extract_functions_from_code(code)

    assert [f["name"] for f in funcs] == ["conditional_func"]


def test_function_extraction_includes_functions_defined_inside_try_block():

    code = """
try:
    def fallback_func(z: int) -> int:
        return z
except ImportError:
    def fallback_func(z: int) -> int:
        return -1
"""

    funcs = extract_functions_from_code(code)

    assert [f["name"] for f in funcs] == ["fallback_func", "fallback_func"]


def test_function_extraction_detects_async_functions():

    code = """
async def fetch_data(url: str) -> dict:
    return {}
"""

    funcs = extract_functions_from_code(code)

    assert funcs[0]["name"] == "fetch_data"
    assert funcs[0]["is_async"] is True


def test_function_extraction_marks_sync_functions_not_async():

    code = """
def add(a: int, b: int) -> int:
    return a + b
"""

    funcs = extract_functions_from_code(code)

    assert funcs[0]["is_async"] is False


def test_function_extraction_excludes_async_class_methods():

    code = """
class Model:
    async def predict(self, x: int) -> int:
        return x
"""

    funcs = extract_functions_from_code(code)

    assert funcs == []


def test_function_extraction_includes_keyword_only_args():

    code = """
def train(data: list, *, epochs: int = 10, lr: float) -> dict:
    return {}
"""

    funcs = extract_functions_from_code(code)

    arg_names = [a["name"] for a in funcs[0]["args"]]

    assert arg_names == ["data", "epochs", "lr"]


def test_function_extraction_marks_positional_vs_keyword_only_kind():

    code = """
def train(data: list, *, epochs: int = 10) -> dict:
    return {}
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["data"]["kind"] == "positional"
    assert args["epochs"]["kind"] == "keyword_only"


def test_function_extraction_keyword_only_default_value():

    code = """
def train(data: list, *, epochs: int = 10) -> dict:
    return {}
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["epochs"]["default"] == 10


def test_function_extraction_required_keyword_only_arg_has_no_default():

    code = """
def train(data: list, *, lr: float) -> dict:
    return {}
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["lr"]["default"] is None
    assert args["lr"]["has_default"] is False


def test_function_extraction_distinguishes_explicit_none_default_from_no_default():
    """`def greet(name, title=None)` and `def greet(name, title)` both end
    up with default=None from ast.literal_eval, but only the first one
    actually has a default -- has_default is what tells them apart.
    """

    code = """
def greet(name: str, title: str = None) -> str:
    return name
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["title"]["default"] is None
    assert args["title"]["has_default"] is True
    assert args["name"]["has_default"] is False


def test_function_extraction_keyword_only_explicit_none_default_has_default_true():

    code = """
def train(data: list, *, callback=None) -> dict:
    return {}
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["callback"]["default"] is None
    assert args["callback"]["has_default"] is True


def test_deduplicate_functions_by_name_keeps_last_definition():

    functions = [
        {"name": "add", "args": [], "return_type": "int", "version": "buggy"},
        {"name": "multiply", "args": [], "return_type": "int", "version": "only"},
        {"name": "add", "args": [], "return_type": "int", "version": "fixed"},
    ]

    deduped = deduplicate_functions_by_name(functions)

    assert len(deduped) == 2
    assert [f["name"] for f in deduped] == ["add", "multiply"]

    add_func = next(f for f in deduped if f["name"] == "add")
    assert add_func["version"] == "fixed"


def test_deduplicate_functions_by_name_no_duplicates_is_unchanged():

    functions = [
        {"name": "add", "args": [], "return_type": "int"},
        {"name": "multiply", "args": [], "return_type": "int"},
    ]

    deduped = deduplicate_functions_by_name(functions)

    assert deduped == functions


def test_function_extraction_includes_positional_only_args():
    """Confirmed exploitable before this fix: only node.args.args and
    node.args.kwonlyargs were read, so positional-only params (those
    before a bare `/`) were silently dropped from extraction entirely --
    the generated endpoint then called notebook_module.f(...) without
    them, raising a TypeError for missing required arguments on every
    request instead of ever exposing them.
    """

    code = """
def f(a, b, /, c, d=1):
    return a + b + c + d
"""

    funcs = extract_functions_from_code(code)

    arg_names = [a["name"] for a in funcs[0]["args"]]

    assert arg_names == ["a", "b", "c", "d"]


def test_function_extraction_positional_only_args_are_marked_positional():

    code = """
def f(a, b, /, c):
    return a + b + c
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["a"]["kind"] == "positional"
    assert args["b"]["kind"] == "positional"


def test_function_extraction_positional_only_default_applies_to_trailing_arg():
    """Defaults apply to the trailing N of posonlyargs+args combined, same
    rule as for a plain positional list -- verify merging posonlyargs in
    doesn't shift which parameter a default is attributed to.
    """

    code = """
def f(a, b=2, /, c=3):
    return a + b + c
"""

    args = {a["name"]: a for a in extract_functions_from_code(code)[0]["args"]}

    assert args["a"]["has_default"] is False
    assert args["b"]["has_default"] is True
    assert args["b"]["default"] == 2
    assert args["c"]["has_default"] is True
    assert args["c"]["default"] == 3


def test_function_extraction_marks_a_literal_default_as_literal():

    code = """
def f(a=2):
    return a
"""

    args = extract_functions_from_code(code)[0]["args"]

    assert args[0]["default_is_literal"] is True
    assert args[0]["default"] == 2


def test_function_extraction_marks_a_non_literal_default_as_not_literal():
    """A default that isn't a literal_eval-able literal (e.g. a
    notebook-defined Enum member) previously stored the same "default" key
    with no way to tell it apart from a real literal default -- the
    generator then repr()'d it as if it were one, turning
    `Priority.HIGH` into the *string* "Priority.HIGH" in the generated
    Pydantic model instead of the actual enum member.
    """

    code = """
def f(priority=Priority.HIGH):
    return priority
"""

    args = extract_functions_from_code(code)[0]["args"]

    assert args[0]["default_is_literal"] is False
    assert args[0]["default"] == "Priority.HIGH"


def test_function_extraction_keyword_only_marks_a_non_literal_default_as_not_literal():

    code = """
def f(*, priority=Priority.HIGH):
    return priority
"""

    args = extract_functions_from_code(code)[0]["args"]

    assert args[0]["default_is_literal"] is False
    assert args[0]["default"] == "Priority.HIGH"


def test_function_extraction_excludes_function_with_var_args():
    """*args can't be represented as a fixed set of Pydantic request
    fields -- the generated endpoint would silently ignore whatever
    callers actually put there. The whole function must be skipped,
    same policy as class methods/nested functions.
    """

    code = """
def f(a, *args):
    return a
"""

    funcs = extract_functions_from_code(code)

    assert funcs == []


def test_function_extraction_excludes_function_with_kwargs():

    code = """
def f(a, **kwargs):
    return a
"""

    funcs = extract_functions_from_code(code)

    assert funcs == []


def test_function_extraction_still_includes_sibling_function_beside_var_args_function():
    """One function using **kwargs must not cause the whole notebook's
    other, perfectly representable functions to be dropped too.
    """

    code = """
def unsupported(a, **kwargs):
    return a

def add(a: int, b: int) -> int:
    return a + b
"""

    funcs = extract_functions_from_code(code)

    assert [f["name"] for f in funcs] == ["add"]


def test_extract_skipped_functions_reports_var_args_with_a_reason():

    code = """
def f(a, *args):
    return a
"""

    skipped = extract_skipped_functions_from_code(code)

    assert skipped == [
        {
            "name": "f",
            "reason": (
                "uses *args/**kwargs, which can't be represented as a "
                "fixed set of request fields"
            ),
        }
    ]


def test_extract_skipped_functions_reports_kwargs_with_a_reason():

    code = """
def f(a, **kwargs):
    return a
"""

    skipped = extract_skipped_functions_from_code(code)

    assert skipped[0]["name"] == "f"
    assert "**kwargs" in skipped[0]["reason"]


def test_extract_skipped_functions_reports_class_methods():

    code = """
class Model:
    def predict(self, x):
        return x
"""

    skipped = extract_skipped_functions_from_code(code)

    assert skipped == [
        {
            "name": "predict",
            "reason": (
                "defined inside a class or nested function, so it isn't "
                "callable as a standalone endpoint"
            ),
        }
    ]


def test_extract_skipped_functions_reports_nested_functions():

    code = """
def outer():
    def inner(x):
        return x
    return inner
"""

    skipped = extract_skipped_functions_from_code(code)

    assert [s["name"] for s in skipped] == ["inner"]


def test_extract_skipped_functions_is_empty_for_a_clean_notebook():

    code = """
def add(a: int, b: int) -> int:
    return a + b
"""

    assert extract_skipped_functions_from_code(code) == []


def test_extract_skipped_functions_does_not_flag_functions_reachable_via_if_block():
    """A def inside a module-level if/try/for/while/with is still callable
    as a plain module-level function once the notebook actually runs (see
    _TRANSPARENT_BODY_FIELDS in ast_parser.py) -- it must not be reported
    as skipped just because it isn't a direct child of the module body.
    """

    code = """
if True:
    def add(a: int, b: int) -> int:
        return a + b
"""

    assert extract_skipped_functions_from_code(code) == []


def test_extract_skipped_functions_skips_unparseable_code_instead_of_raising():

    code = "def f(:\n"

    assert extract_skipped_functions_from_code(code) == []


def test_normalize_type_annotation_strips_optional_wrapping_a_plain_type():

    assert normalize_type_annotation("Optional[str]") == "str"


def test_normalize_type_annotation_strips_union_keeping_the_first_type():

    assert normalize_type_annotation("Union[int, float]") == "int"


def test_normalize_type_annotation_strips_pep604_union_keeping_the_first_type():

    assert normalize_type_annotation("int | str") == "int"


def test_normalize_type_annotation_reduces_list_and_lowercase_list_to_list():

    assert normalize_type_annotation("List[int]") == "list"
    assert normalize_type_annotation("list[int]") == "list"


def test_normalize_type_annotation_reduces_dict_tuple_set_to_their_bare_names():

    assert normalize_type_annotation("Dict[str, int]") == "dict"
    assert normalize_type_annotation("Tuple[int, str]") == "tuple"
    assert normalize_type_annotation("Set[int]") == "set"


def test_normalize_type_annotation_strips_annotated_keeping_the_first_type():

    assert normalize_type_annotation('Annotated[int, "meta"]') == "int"


def test_normalize_type_annotation_passes_through_a_plain_type_unchanged():

    assert normalize_type_annotation("int") == "int"


def test_normalize_type_annotation_passes_through_none_and_empty_string():

    assert normalize_type_annotation(None) is None
    assert normalize_type_annotation("") == ""


def test_normalize_type_annotation_resolves_a_nested_generic_inside_optional():
    """Confirmed exploitable before this fix: peeling "Optional[" off
    "Optional[List[int]]" via ".replace('Optional[', '').replace(']', '')"
    strips *every* closing bracket in the string, not just the one
    belonging to Optional's own wrapper -- corrupting "List[int]]" into
    the mismatched "List[int" instead of "List[int]". That matched none
    of the List[/Dict[/Tuple[/Set[ checks, so a field typed
    `Optional[List[float]]` silently got a `None` example instead of a
    list.
    """

    assert normalize_type_annotation("Optional[List[int]]") == "list"
    assert normalize_type_annotation("Optional[Dict[str, int]]") == "dict"


def test_normalize_type_annotation_resolves_a_nested_generic_inside_union():

    assert normalize_type_annotation("Union[List[float], str]") == "list"


def test_normalize_type_annotation_resolves_a_nested_generic_before_pep604_none():
    """The same corruption class as Optional[List[...]], for the PEP 604
    spelling: peeling the union without re-normalizing the surviving
    branch left "List[int]" un-reduced to "list".
    """

    assert normalize_type_annotation("List[int] | None") == "list"


def test_normalize_type_annotation_resolves_a_nested_generic_inside_annotated():

    assert normalize_type_annotation('Annotated[List[int], "meta"]') == "list"


def test_normalize_type_annotation_does_not_split_a_pipe_nested_inside_a_generic():
    """A "|" inside a generic's own arguments (e.g. a PEP 604 union used
    as a Dict value type) is not a top-level union of the whole
    annotation and must not be split there -- the annotation is still a
    Dict overall.
    """

    assert normalize_type_annotation("Dict[str, int | float]") == "dict"


def test_generate_example_response_defaults_to_a_list_for_optional_list_return_type():

    assert generate_example_response("Optional[List[int]]") == {"result": []}


def test_generate_example_response_defaults_to_none_result_for_no_return_type():

    assert generate_example_response(None) == {"result": None}


def test_generate_example_response_uses_the_literals_first_value():

    assert generate_example_response('Literal["xgboost", "rf"]') == {
        "result": "xgboost"
    }


def test_generate_example_payload_defaults_to_a_list_for_an_optional_list_param():
    """Before this fix, a parameter typed `Optional[List[float]]` (an
    extremely common real-world signature, e.g. `scores: Optional[List[float]]
    = None`) produced an example_payload of `None` for that field instead
    of a representative empty list -- misleading in both `inspect`'s
    output and the example baked into the generated app's own OpenAPI
    schema.
    """

    payload = generate_example_payload(
        [{"name": "scores", "type": "Optional[List[float]]"}]
    )

    assert payload == {"scores": []}


def test_generate_example_payload_uses_an_explicit_default_over_the_type_default():

    payload = generate_example_payload(
        [{"name": "count", "type": "int", "default": 5}]
    )

    assert payload == {"count": 5}


def test_generate_example_payload_uses_the_literals_first_value():

    payload = generate_example_payload(
        [{"name": "model", "type": 'Literal["xgboost", "rf"]'}]
    )

    assert payload == {"model": "xgboost"}


def test_extract_imports_maps_import_names_to_their_real_pypi_package_names():
    """None of the entries in extract_imports_from_code's pypi_mapping
    had any test coverage before this -- verified here for every mapped
    name, not just the ones this fix adds, so a future change to the
    table is automatically covered too.
    """

    code = (
        "import sklearn\n"
        "import cv2\n"
        "from PIL import Image\n"
        "import yaml\n"
    )

    assert extract_imports_from_code(code) == {
        "scikit-learn",
        "opencv-python",
        "Pillow",
        "PyYAML",
    }


def test_extract_imports_maps_dangerously_ambiguous_import_names():
    """Confirmed missing before this fix: PyPI hosts a real, unrelated,
    unofficial package under each of these bare import names -- the same
    hazard class STANDARD_LIBS already exists to close for stdlib names
    like "asyncio" (see test_standard_libs_covers_common_stdlib_modules_beyond_the_old_hardcoded_list
    in test_compiler.py). Left unmapped, `pip install <import name>`
    silently installs the *wrong* package into requirements.txt instead
    of the one the notebook actually needs.
    """

    code = (
        "from dotenv import load_dotenv\n"
        "import jwt\n"
        "import serial\n"
        "import docx\n"
    )

    assert extract_imports_from_code(code) == {
        "python-dotenv",
        "PyJWT",
        "pyserial",
        "python-docx",
    }


def test_extract_imports_maps_a_submodule_import_by_its_top_level_package():
    """`import cv2.aruco` or `from PIL.Image import open` must still
    resolve through pypi_mapping via the *top-level* module name, the
    same as a plain `import cv2`/`from PIL import Image` would.
    """

    code = (
        "import cv2.aruco\n"
        "from PIL.Image import open\n"
    )

    assert extract_imports_from_code(code) == {"opencv-python", "Pillow"}


def test_extract_imports_leaves_an_unmapped_third_party_name_unchanged():

    code = "import pandas\n"

    assert extract_imports_from_code(code) == {"pandas"}