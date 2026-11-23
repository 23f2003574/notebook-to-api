"""Plain return annotations are published as OpenAPI result schemas; Decimal handling is explicit."""
import importlib
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

SOURCE = '''
from typing import Optional, List, Dict
from decimal import Decimal
import datetime, enum, uuid
from typing import Iterator, Generator, AsyncIterator

def i(a: int) -> int:
    return a

def fl(a: float) -> float:
    return a

def st(a: str) -> str:
    return a

def bo(a: bool) -> bool:
    return a

def li(a: int) -> List[int]:
    return [a]

def di(a: int) -> Dict[str, float]:
    return {"x": 1.5}

def op(a: Optional[int] = None) -> Optional[int]:
    return a

def dec(s: str) -> Decimal:
    return Decimal(s)

def decs(s: str) -> List[Decimal]:
    return [Decimal(s)]

def bare(a: int) -> list:
    return [a]

def untyped(a: int):
    return a

class Color(enum.Enum):
    RED = "red"

def gen(n: int) -> Iterator[int]:
    yield from range(n)

def gen3(n: int) -> Generator[str, None, None]:
    yield "a"

async def agen(n: int) -> AsyncIterator[int]:
    yield n

def day(a: int) -> datetime.date:
    return datetime.date(2026, 1, a)

def stamp(a: int) -> datetime.datetime:
    return datetime.datetime(2026, 1, 1, 12, 0, a)

def ident(a: int) -> uuid.UUID:
    return uuid.UUID(int=a)

def color(a: int) -> Color:
    return Color.RED

def blob(a: int) -> bytes:
    return b"ab" * a

def nan(a: int) -> float:
    return float("nan")
'''
H = {"X-API-Key": "schema-key"}


@pytest.fixture
def make(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "schema_app"))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "schema-key")

    def build(**env):
        monkeypatch.delenv("NOTEBOOK_API_DECIMAL_AS_STRING", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for name in [k for k in sys.modules if k.startswith("schema_app")]:
            del sys.modules[name]
        return TestClient(importlib.import_module("schema_app.app").app)

    return build


def _result(client, path):
    spec = client.get("/openapi.json", headers=H).json()
    return spec["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["result"]


def test_plain_types_have_meaningful_schemas(make):
    c = make()
    assert _result(c, "/i") == {"type": "integer"}
    # NaN and infinity cannot be JSON numbers; the app sends null, so the schema says so
    assert _result(c, "/fl") == {"anyOf": [{"type": "number"}, {"type": "null"}]}
    assert _result(c, "/st") == {"type": "string"}
    assert _result(c, "/bo") == {"type": "boolean"}
    assert _result(c, "/li") == {"type": "array", "items": {"type": "integer"}}
    assert _result(c, "/di") == {"type": "object", "additionalProperties": {"anyOf": [{"type": "number"}, {"type": "null"}]}}
    assert _result(c, "/op") == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert _result(c, "/bare")["type"] == "array"


def test_unannotated_results_stay_open(make):
    assert _result(make(), "/untyped") == {}


def test_decimal_schema_matches_default_behaviour(make):
    c = make()
    assert _result(c, "/dec") == {"anyOf": [{"type": "number"}, {"type": "string"}]}
    assert _result(c, "/decs")["items"] == {"anyOf": [{"type": "number"}, {"type": "string"}]}
    assert c.post("/dec", json={"s": "19.99"}, headers=H).json() == {"result": 19.99}
    assert c.post("/dec", json={"s": "12345678901234567890.123456789"}, headers=H).json() == {
        "result": "12345678901234567890.123456789"
    }


def test_decimal_as_string_is_consistent_and_declared(make):
    c = make(NOTEBOOK_API_DECIMAL_AS_STRING="true")
    assert _result(c, "/dec") == {"type": "string"}
    assert _result(c, "/decs") == {"type": "array", "items": {"type": "string"}}
    for text in ("19.99", "3", "12345678901234567890.123456789", "0.1"):
        assert c.post("/dec", json={"s": text}, headers=H).json() == {"result": text}
    assert c.post("/decs", json={"s": "2.50"}, headers=H).json() == {"result": ["2.50"]}


def test_endpoint_values_still_match_their_schemas(make):
    c = make()
    assert c.post("/li", json={"a": 4}, headers=H).json() == {"result": [4]}
    assert c.post("/op", json={}, headers=H).json() == {"result": None}
    assert c.post("/di", json={"a": 1}, headers=H).json() == {"result": {"x": 1.5}}


def test_generators_dates_enums_uuids_and_bytes_have_typed_schemas(make):
    c = make()
    assert _result(c, "/gen") == {"type": "array", "items": {"type": "integer"}}
    assert _result(c, "/gen3") == {"type": "array", "items": {"type": "string"}}
    assert _result(c, "/agen") == {"type": "array", "items": {"type": "integer"}}
    assert _result(c, "/day") == {"type": "string", "format": "date"}
    assert _result(c, "/stamp") == {"type": "string", "format": "date-time"}
    assert _result(c, "/ident") == {"type": "string", "format": "uuid"}
    assert _result(c, "/color")["enum"] == ["red"]
    assert _result(c, "/blob") == {"type": "string"}


def test_published_schemas_describe_what_the_endpoints_return(make):
    from jsonschema import validate
    c = make()
    for path, body in (("/gen", {"n": 3}), ("/gen3", {"n": 1}), ("/agen", {"n": 2}), ("/day", {"a": 5}),
                       ("/stamp", {"a": 7}), ("/ident", {"a": 9}), ("/color", {"a": 1}), ("/blob", {"a": 2}),
                       ("/nan", {"a": 1}), ("/fl", {"a": 1.5})):
        response = c.post(path, json=body, headers=H)
        assert response.status_code == 200, (path, response.text)
        validate(response.json()["result"], _result(c, path))
    assert c.post("/nan", json={"a": 1}, headers=H).json() == {"result": None}


def test_sdks_keep_the_declared_result_types(make, tmp_path):
    import json
    import subprocess

    c = make()
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(c.get("/openapi.json", headers=H).json()))
    for language, suffix, expected in (
        ("python", "py", ("class GenResponse(TypedDict):\n    result: List[int]", "class NanResponse(TypedDict):\n    result: Optional[float]")),
        ("typescript", "ts", ("export interface GenResponse {\n  result: number[];", "export interface DayResponse {\n  result: string;")),
    ):
        out = tmp_path / f"sdk.{suffix}"
        done = subprocess.run([sys.executable, "-m", "backend.cli", "export-sdk", "--openapi", str(spec),
                               "--language", language, "--output", str(out)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        text = out.read_text()
        for snippet in expected:
            assert snippet in text, (language, snippet)
        assert "class DayResponse(TypedDict):\n    result: Any" not in text
