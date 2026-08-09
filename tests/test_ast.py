from backend.parser.ast_parser import (
    extract_functions_from_code,
    extract_imports_from_code,
    deduplicate_functions_by_name
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