"""`bytes` / BytesIO request parameters are base64 strings: published as such, decoded strictly."""
import json
import os
import subprocess
import sys

import nbformat
import pytest

from backend.compiler import compile_notebook

SRC = '''
import io

def size(b: bytes) -> int:
    return len(b)

def read_all(f: io.BytesIO) -> bytes:
    return f.read()

def with_default(b: bytes = b"abc") -> int:
    return len(b)
'''
RUNNER = """
import json, sys
from fastapi.testclient import TestClient
import svc.app as m
c = TestClient(m.app)
h = {"X-API-Key": "k" * 20}
out = {}
for name, path, body in json.loads(sys.argv[1]):
    r = c.post(path, json=body, headers=h)
    out[name] = [r.status_code, r.text]
out["spec"] = c.get("/openapi.json", headers=h).json()["components"]["schemas"]["SizeRequest"]
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("b64")
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SRC)]), root / "nb.ipynb")
    compile_notebook(str(root / "nb.ipynb"), str(root / "svc"))
    calls = [
        ["hi", "/size", {"b": "aGk="}], ["empty", "/size", {"b": ""}],
        ["binary", "/size", {"b": "//79AA=="}],  # 0xFF 0xFE 0xFD 0x00, not UTF-8
        ["uri", "/size", {"b": "data:application/octet-stream;base64,aGk="}],
        ["text", "/size", {"b": "hello world!"}], ["pad", "/size", {"b": "aGk"}],
        ["stream", "/read_all", {"f": "aGk="}], ["default", "/with_default", {}],
    ]
    environ = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
    environ["NOTEBOOK_API_KEY"] = "k" * 20
    proc = subprocess.run([sys.executable, "-c", RUNNER, json.dumps(calls)], cwd=root, env=environ,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_base64_round_trips_to_the_exact_bytes(result):
    assert result["hi"][0] == 200 and json.loads(result["hi"][1]) == {"result": 2}  # "hi", not 4 characters
    assert json.loads(result["empty"][1]) == {"result": 0}
    assert json.loads(result["binary"][1]) == {"result": 4}
    assert json.loads(result["stream"][1])["result"] is not None and result["stream"][0] == 200


def test_data_uri_and_python_defaults_still_work(result):
    assert json.loads(result["uri"][1]) == {"result": 2}
    assert json.loads(result["default"][1]) == {"result": 3}


def test_text_that_is_not_base64_is_rejected_not_reinterpreted(result):
    for name in ("text", "pad"):
        assert result[name][0] == 422 and "base64" in result[name][1], name


def test_openapi_declares_the_encoding(result):
    prop = result["spec"]["properties"]["b"]
    assert prop["type"] == "string" and prop["contentEncoding"] == "base64" and prop["format"] == "byte"
    assert "base64" in prop["description"]


def test_sdks_type_bytes_as_a_string(tmp_path):
    from backend.exporters.sdk_generator import generate_python_sdk, generate_typescript_sdk
    spec = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {"/size": {"post": {
        "operationId": "size", "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/SizeRequest"}}}},
        "responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"SizeRequest": {"type": "object", "required": ["b"], "properties": {
            "b": {"type": "string", "format": "byte", "contentEncoding": "base64"}}}}}}
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(spec))
    generate_python_sdk(str(path), str(tmp_path / "c.py"))
    generate_typescript_sdk(str(path), str(tmp_path / "c.ts"))
    assert "b: str" in (tmp_path / "c.py").read_text() and "b: string" in (tmp_path / "c.ts").read_text()
