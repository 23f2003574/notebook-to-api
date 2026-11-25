"""Unannotated functions publish an inferred (or honestly open) result schema, never a false "None"."""
import ast
import json
import subprocess
import sys

import nbformat
import pytest

from backend.compiler import compile_notebook
from backend.parser.ast_parser import infer_return_type

SRC = '''
def untyped(x):
    return {"x": x}

def doubled(x: int):
    return [x, x * 2]

def label(x: int):
    if x > 1:
        return "big"
    return f"small {x}"

def number(x: int):
    if x:
        return 1
    return 2.5

def maybe(x: int):
    if x:
        return {"k": x}
    return None

def mixed(x: int):
    if x:
        return 1
    return "one"

def opaque(x: int):
    return x + 1

def stream(n: int):
    for i in range(n):
        yield i

def counted(x: int):
    yield 1
    yield 2

def nothing(x: int):
    print(x)

def typed(x: int) -> int:
    return x
'''


def _infer(code):
    return infer_return_type(ast.parse(code).body[0])


@pytest.mark.parametrize("code,expected", [
    ("def f():\n    return {'a': 1}", ("Dict[str, Any]", True)),
    ("def f():\n    return [1]", ("List[Any]", True)),
    ("def f():\n    return 'a'", ("str", True)),
    ("def f(x):\n    return f'{x}'", ("str", True)),
    ("def f():\n    return True", ("bool", True)),
    ("def f(x):\n    if x:\n        return 1\n    return 2.0", ("float", True)),
    ("def f(x):\n    if x:\n        return 1\n    return None", ("Optional[int]", True)),
    ("def f(x):\n    if x:\n        return 1\n    return 'a'", (None, True)),
    ("def f(x):\n    return x", (None, True)),
    ("def f():\n    yield 1", ("List[int]", True)),
    ("def f():\n    pass", (None, False)),
    ("def f():\n    def g():\n        return 1\n    return None", (None, False)),
])
def test_inference_only_trusts_literal_returns(code, expected):
    assert _infer(code) == expected


@pytest.fixture(scope="module")
def spec(tmp_path_factory):
    root = tmp_path_factory.mktemp("infer")
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SRC)])
    nbformat.write(nb, root / "nb.ipynb")
    compile_notebook(str(root / "nb.ipynb"), str(root / "svc"))
    code = "import json, svc.app as a; print(json.dumps(a.app.openapi()))"
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout), root


def _ok(spec_and_root, name):
    return spec_and_root[0]["paths"][f"/{name}"]["post"]["responses"]["200"]


def _result(spec_and_root, name):
    return _ok(spec_and_root, name)["content"]["application/json"]["schema"]["properties"]["result"]


def test_inferred_results_are_typed_and_described(spec):
    assert _result(spec, "untyped") == {"additionalProperties": True, "type": "object"}
    assert _result(spec, "doubled")["type"] == "array"
    assert _result(spec, "label")["type"] == "string"
    assert "number" in json.dumps(_result(spec, "number"))
    assert "object" in json.dumps(_result(spec, "maybe")) and "null" in json.dumps(_result(spec, "maybe"))
    assert _ok(spec, "untyped")["description"] == "Returns Dict[str, Any]"


def test_unknown_shapes_say_any_not_none(spec):
    for name in ("mixed", "opaque"):
        assert _result(spec, name) == {}
        assert _ok(spec, name)["description"] == "Returns Any", name
    assert _ok(spec, "nothing")["description"] == "Returns None"
    assert _ok(spec, "typed")["description"] == "Returns int"


@pytest.mark.parametrize("code,expected", [
    ("def f(n):\n    for i in range(n):\n        yield i", "List[Any]"),  # a name: element type unknown
    ("def f():\n    yield 1\n    yield 2", "List[int]"),
    ("def f():\n    yield 1\n    yield 2.5", "List[float]"),
    ("def f():\n    yield 'a'\n    yield 'b'", "List[str]"),
    ("def f():\n    yield 1\n    yield 'a'", "List[Any]"),
    ("def f():\n    yield (1, 2)", "List[List[Any]]"),
    ("def f(n):\n    yield from range(n)", "List[int]"),
    ("def f():\n    yield from [1, 2, 3]", "List[int]"),
    ("def f(xs):\n    yield from xs", "List[Any]"),
    ("def f():\n    yield\n", "List[Any]"),
    ("def f():\n    yield 1\n    return 'ignored'", "List[int]"),
    ("async def f():\n    yield 1", None),
])
def test_generator_results_are_arrays(code, expected):
    assert _infer(code)[0] == expected


def test_generator_endpoint_publishes_an_array_schema(spec):
    stream = _result(spec, "stream")
    assert stream == {"type": "array", "items": {}}
    assert _ok(spec, "stream")["description"] == "Returns List[Any]"
    assert _result(spec, "counted") == {"type": "array", "items": {"type": "integer"}}


def test_python_and_typescript_sdks_follow_the_published_types(spec):
    from backend.exporters.sdk_generator import generate_python_sdk, generate_typescript_sdk

    openapi, root = spec
    path = root / "openapi.json"
    path.write_text(json.dumps(openapi))
    generate_python_sdk(str(path), str(root / "client.py"))
    generate_typescript_sdk(str(path), str(root / "client.ts"))
    python_sdk, ts_sdk = (root / "client.py").read_text(), (root / "client.ts").read_text()
    assert "class UntypedResponse" in python_sdk and "result: Dict[str, Any]" in python_sdk
    assert "interface UntypedResponse" in ts_sdk and "result: Record<string, unknown>" in ts_sdk or "result: {" in ts_sdk
    compile(python_sdk, "sdk.py", "exec")
