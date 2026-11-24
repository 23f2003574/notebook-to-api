"""User-defined return types get real response schemas in OpenAPI and in both SDKs."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook
from backend.exporters.sdk_generator import generate_python_sdk, generate_typescript_sdk

ROOT = Path(__file__).resolve().parents[1]
CODE = '''
from dataclasses import dataclass
from typing import Dict, List, Optional, TypedDict
from pydantic import BaseModel

@dataclass
class Point:
    x: float
    y: float

@dataclass
class Segment:
    start: Point
    end: Point

class Box(BaseModel):
    name: str
    corner: Optional[Point] = None

class Stats(TypedDict):
    n: int
    mean: float

def origin() -> Point: return Point(0, 0)
def many(n: int) -> List[Point]: return [Point(i, i) for i in range(n)]
def maybe(n: int) -> Optional[Point]: return None if n == 0 else Point(n, n)
def seg() -> Segment: return Segment(Point(0, 0), Point(1, 1))
def box() -> Box: return Box(name="b", corner=Point(2, 2))
def stats() -> Stats: return {"n": 1, "mean": 0.5}
def by_name() -> Dict[str, Point]: return {"a": Point(1, 1)}
def plain(x: int) -> int: return x
def untyped(): return {"anything": 1}
'''


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("typed")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(CODE))
    nbformat.write(nb, tmp / "nb.ipynb")
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "typedapp"))
    patch = pytest.MonkeyPatch()
    patch.setenv("NOTEBOOK_API_KEY", "k")  # restored when the module finishes, even on failure
    sys.path.insert(0, str(tmp))
    spec = importlib.util.spec_from_file_location("typedapp_app", tmp / "typedapp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["typedapp_app"] = module
    spec.loader.exec_module(module)
    client = TestClient(module.app)
    schema = client.get("/openapi.json").json()
    (tmp / "openapi.json").write_text(json.dumps(schema))
    yield tmp, client, schema
    sys.modules.pop("typedapp_app", None)
    sys.path.remove(str(tmp))
    patch.undo()


def _result(schema, path):
    return schema["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["result"]


def test_openapi_publishes_component_references(built):
    _, _, schema = built
    assert _result(schema, "/origin") == {"$ref": "#/components/schemas/Point"}
    assert _result(schema, "/many") == {"items": {"$ref": "#/components/schemas/Point"}, "type": "array"}
    assert {"$ref": "#/components/schemas/Point"} in _result(schema, "/maybe")["anyOf"]
    assert {"type": "null"} in _result(schema, "/maybe")["anyOf"]
    assert _result(schema, "/seg") == {"$ref": "#/components/schemas/Segment"}
    components = schema["components"]["schemas"]
    assert {"Point", "Segment", "Box", "Stats"} <= set(components)
    assert components["Segment"]["properties"]["start"] == {"$ref": "#/components/schemas/Point"}
    assert components["Point"]["required"] == ["x", "y"]


def test_untyped_results_are_inferred_from_literals_and_primitives_are_typed(built):
    _, _, schema = built
    assert _result(schema, "/untyped") == {"additionalProperties": True, "type": "object"}  # inferred from the literal dict return
    assert _result(schema, "/plain") == {"type": "integer"}  # a primitive annotation is published (see test_openapi_result_schemas)


def test_runtime_responses_match_the_published_shapes(built):
    _, client, _ = built
    h = {"X-API-Key": "k"}
    assert client.post("/origin", json={}, headers=h).json() == {"result": {"x": 0.0, "y": 0.0}}
    assert client.post("/many", json={"n": 2}, headers=h).json()["result"] == [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]
    assert client.post("/maybe", json={"n": 0}, headers=h).json() == {"result": None}
    assert client.post("/seg", json={}, headers=h).json()["result"]["end"] == {"x": 1.0, "y": 1.0}
    assert client.post("/box", json={}, headers=h).json()["result"] == {"name": "b", "corner": {"x": 2.0, "y": 2.0}}
    assert client.post("/stats", json={}, headers=h).json()["result"] == {"n": 1, "mean": 0.5}
    assert client.post("/by_name", json={}, headers=h).json()["result"] == {"a": {"x": 1.0, "y": 1.0}}


def test_python_sdk_types_results(built):
    tmp, _, _ = built
    generate_python_sdk(str(tmp / "openapi.json"), str(tmp / "client.py"))
    code = (tmp / "client.py").read_text()
    assert "class OriginResponse(TypedDict):\n    result: PointType" in code
    assert "result: List[PointType]" in code and "result: Optional[PointType]" in code
    assert "result: SegmentType" in code and "result: BoxType" in code and "result: StatsType" in code
    assert "class PlainResponse(TypedDict):\n    result: int" in code
    assert code.index("class PointType") < code.index("class OriginResponse")
    compile(code, "client.py", "exec")


def test_typescript_sdk_types_results_and_compiles_strictly(built):
    tmp, _, _ = built
    generate_typescript_sdk(str(tmp / "openapi.json"), str(tmp / "client.ts"))
    ts = (tmp / "client.ts").read_text()
    assert 'result: { "x": number; "y": number };' in ts
    assert 'result: { "x": number; "y": number }[];' in ts
    assert "| null;" in ts
    if shutil.which("tsc"):
        done = subprocess.run(["tsc", "--noEmit", "--strict", "--target", "es2020", "--lib", "es2020,dom",
                               "--module", "esnext", "--moduleResolution", "bundler", str(tmp / "client.ts")],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
