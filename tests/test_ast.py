from backend.parser.ast_parser import (
    extract_functions_from_code,
    extract_imports_from_code
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