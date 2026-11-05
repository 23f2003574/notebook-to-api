"""Functions differing only by case (`Predict` / `predict`) must get distinct
SDK request/response types instead of redeclaring one name twice."""
import re

import nbformat

from backend.compiler import compile_notebook
from backend.exporters.sdk_generator import (
    generate_python_sdk,
    generate_typescript_sdk,
)


def _compile(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_code_cell(
            "def Predict(x: int) -> int:\n    '''d'''\n    return x\n"
            "def predict(s: str) -> str:\n    '''d'''\n    return s\n"
        )
    ]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    out = tmp_path / "gen_sdk_case_app"
    compile_notebook(str(path), str(out))
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        import importlib
        mod = importlib.import_module("gen_sdk_case_app.app")
        schema = mod.app.openapi()
    finally:
        sys.path.remove(str(tmp_path))
        for k in [k for k in sys.modules if k.startswith("gen_sdk_case_app")]:
            del sys.modules[k]
    return schema


def _generate(fn, tmp_path, schema, name):
    import json
    spec = tmp_path / "openapi.json"
    spec.write_text(json.dumps(schema))
    out = tmp_path / name
    fn(str(spec), str(out))
    return out.read_text()


def test_python_sdk_declares_each_request_type_once(tmp_path):
    code = _generate(generate_python_sdk, tmp_path, _compile(tmp_path), "sdk.py")
    names = re.findall(r"^class (\w+Request)\(", code, re.M)
    assert len(names) == len(set(names))
    assert "PredictRequest" in names and "Predict2Request" in names


def test_typescript_sdk_declares_each_request_type_once(tmp_path):
    code = _generate(generate_typescript_sdk, tmp_path, _compile(tmp_path), "sdk.ts")
    names = re.findall(r"^export interface (\w+Request)\b", code, re.M)
    assert len(names) == len(set(names))
    assert "PredictRequest" in names and "Predict2Request" in names
