"""An optional Callable parameter accepts only null, never arbitrary JSON as a "callback"."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import nbformat
from fastapi.testclient import TestClient

from backend.parser.ast_parser import extract_functions_from_code

ROOT = Path(__file__).resolve().parents[1]
CODE = (
    "from typing import Callable, Optional\n"
    "def run(a: int, cb: Optional[Callable] = None, c: int = 5, *, kw: Callable = None):\n"
    "    if cb is not None or kw is not None:\n        raise RuntimeError('callback was supplied')\n"
    "    return [a, c]\n"
)


def test_parser_types_the_field_as_none_only():
    args = {a["name"]: a for a in extract_functions_from_code(CODE)[0]["args"]}
    assert args["cb"]["type"] == "None" and args["kw"]["type"] == "None"
    assert args["a"]["type"] == "int" and args["c"]["type"] == "int"


def test_generated_endpoint_rejects_json_callbacks_and_keeps_the_default(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(CODE))
    nbformat.write(nb, tmp_path / "nb.ipynb")
    done = subprocess.run([sys.executable, "-m", "backend.cli", "compile", str(tmp_path / "nb.ipynb"),
                           "--output", str(tmp_path / "optcallbackapp")], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert done.returncode == 0, done.stderr
    monkeypatch.setenv("NOTEBOOK_API_KEY", "k")  # restored after the test
    sys.path.insert(0, str(tmp_path))
    try:
        spec = importlib.util.spec_from_file_location("optcallbackapp_app", tmp_path / "optcallbackapp" / "app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["optcallbackapp_app"] = module
        spec.loader.exec_module(module)
        client = TestClient(module.app)
        headers = {"X-API-Key": "k"}
        assert client.post("/run", json={"a": 1}, headers=headers).json() == {"result": [1, 5]}
        assert client.post("/run", json={"a": 1, "cb": None}, headers=headers).status_code == 200
        assert client.post("/run", json={"a": 1, "cb": 5}, headers=headers).status_code == 422
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("optcallbackapp_app", None)
