"""Background endpoints publish their eventual result type, and both SDKs use it."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook
from backend.exporters.sdk_generator import generate_python_sdk, generate_typescript_sdk

CODE = '''
from dataclasses import dataclass
from typing import List, Optional
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

def train_point() -> Point: return Point(1, 2)
def train_points(n: int = 2) -> List[Point]: return [Point(i, i) for i in range(n)]
def train_maybe(n: int = 0) -> Optional[Point]: return None if n == 0 else Point(n, n)
def train_segment() -> Segment: return Segment(Point(0, 0), Point(1, 1))
def train_box() -> Box: return Box(name="b", corner=Point(3, 3))
def train_total(n: int = 3) -> int: return n
def train_untyped(): return {"anything": 1}
'''


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("taskresults")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(CODE))
    nbformat.write(nb, tmp / "nb.ipynb")
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "taskresapp"))
    patch = pytest.MonkeyPatch()
    patch.setenv("NOTEBOOK_API_KEY", "k")
    patch.syspath_prepend(str(tmp))
    spec = importlib.util.spec_from_file_location("taskresapp_app", tmp / "taskresapp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["taskresapp_app"] = module
    spec.loader.exec_module(module)
    client = TestClient(module.app)
    schema = client.get("/openapi.json").json()
    (tmp / "openapi.json").write_text(json.dumps(schema))
    yield tmp, client, schema
    sys.modules.pop("taskresapp_app", None)
    patch.undo()


def _ext(schema, path):
    return schema["paths"][path]["post"].get("x-notebook-to-api-result-schema")


def test_openapi_publishes_each_background_result_schema(built):
    _, _, schema = built
    assert _ext(schema, "/train_point") == {"$ref": "#/components/schemas/Point"}
    assert _ext(schema, "/train_points") == {"items": {"$ref": "#/components/schemas/Point"}, "type": "array"}
    assert {"type": "null"} in _ext(schema, "/train_maybe")["anyOf"]
    assert _ext(schema, "/train_segment") == {"$ref": "#/components/schemas/Segment"}
    assert _ext(schema, "/train_box") == {"$ref": "#/components/schemas/Box"}
    components = schema["components"]["schemas"]
    assert components["Segment"]["properties"]["end"] == {"$ref": "#/components/schemas/Point"}
    assert components["Box"]["properties"]["corner"]["anyOf"][0] == {"$ref": "#/components/schemas/Point"}
    assert _ext(schema, "/train_total") is None and _ext(schema, "/train_untyped") is None  # primitives keep the open result


def test_the_submission_envelope_is_unchanged(built):
    _, _, schema = built
    ok = schema["paths"]["/train_point"]["post"]["responses"]["200"]
    assert "task_id" in json.dumps(ok) or ok["description"]


def test_openapi_is_valid(built):
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(built[2])


def test_real_task_results_match_the_published_schemas(built):
    _, client, _ = built
    headers = {"X-API-Key": "k"}

    def result(path, body=None):
        task_id = client.post(path, json=body or {}, headers=headers).json()["task_id"]
        for _ in range(100):
            task = client.get(f"/tasks/{task_id}", headers=headers).json()
            if task["status"] != "processing":
                return task["result"]
            time.sleep(0.05)
        raise AssertionError("task did not finish")

    assert result("/train_point") == {"x": 1.0, "y": 2.0}
    assert result("/train_points", {"n": 2}) == [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]
    assert result("/train_maybe") is None and result("/train_maybe", {"n": 2}) == {"x": 2.0, "y": 2.0}
    assert result("/train_segment")["end"] == {"x": 1.0, "y": 1.0}
    assert result("/train_box") == {"name": "b", "corner": {"x": 3.0, "y": 3.0}}


def test_python_sdk_types_task_results(built):
    tmp, _, _ = built
    generate_python_sdk(str(tmp / "openapi.json"), str(tmp / "client.py"))
    code = (tmp / "client.py").read_text()
    task = lambda name: code[code.index(f"class {name}TaskResult"):].split("\n\n")[0]  # noqa: E731
    assert "result: PointType" in task("TrainPoint")
    assert "result: List[PointType]" in task("TrainPoints")
    assert "result: Optional[PointType]" in task("TrainMaybe")
    assert "result: SegmentType" in task("TrainSegment") and "result: BoxType" in task("TrainBox")
    assert "result: int" in task("TrainTotal")
    assert code.index("class PointType") < code.index("class TrainPointTaskResult")
    compile(code, "client.py", "exec")


def test_typescript_sdk_types_task_results_and_compiles_strictly(built):
    tmp, _, _ = built
    generate_typescript_sdk(str(tmp / "openapi.json"), str(tmp / "client.ts"))
    ts = (tmp / "client.ts").read_text()
    block = lambda name: ts[ts.index(f"export interface {name}TaskResult"):].split("}\n")[0]  # noqa: E731
    assert 'result?: { "x": number; "y": number };' in block("TrainPoint")
    assert 'result?: { "x": number; "y": number }[];' in block("TrainPoints")
    assert "| null;" in block("TrainMaybe")
    if shutil.which("tsc"):
        done = subprocess.run(["tsc", "--noEmit", "--strict", "--target", "es2020", "--lib", "es2020,dom", "--module", "esnext",
                               "--moduleResolution", "bundler", str(tmp / "client.ts")], capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
