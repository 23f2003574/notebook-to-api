import ast
import json
import subprocess
import sys
import types
from pathlib import Path

from backend.exporters.sdk_generator import generate_python_sdk

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_schema(tmp_path, paths):
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(
        json.dumps({"paths": paths}), encoding="utf-8"
    )
    return schema_path


def test_generate_python_sdk_produces_syntactically_valid_python(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert "class NotebookAPIClient:" in source
    assert "def train_model(self, payload: dict):" in source


def test_generate_python_sdk_skips_non_post_endpoints(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {"post": {"operationId": "train_model"}},
            "/health": {"get": {"operationId": "health_check"}},
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "def train_model(" in source
    assert "def health_check(" not in source


def test_generate_python_sdk_sends_api_key_header(tmp_path):
    """The generated app rejects every request without an X-API-Key header
    (see verify_api_key in api_generator.py). The generated client must
    send one by default instead of silently 401ing on every call.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert '"X-API-Key": self.api_key' in source
    assert "notebook-to-api-dev-key" in source


def test_generate_python_sdk_method_name_handles_multi_segment_paths(tmp_path):
    """Real compiled apps expose built-in multi-segment POST paths like
    /tasks/cleanup and /tasks/reset alongside notebook-derived endpoints.
    A naive path-to-identifier conversion that only strips the leading
    slash leaves the rest of the slashes in place, producing an invalid
    `def tasks/cleanup(...)`.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/tasks/cleanup": {"post": {"operationId": "cleanup_tasks"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert "def tasks_cleanup(self, payload: dict):" in source


def test_generate_python_sdk_client_sends_correct_request(tmp_path, monkeypatch):
    """Load the generated client in isolation (mocking requests.post) to
    confirm the method actually builds the right URL/payload/header,
    rather than only checking the source text contains the right tokens.

    `requests` is a dependency of the *generated* client, not of this
    repo, so a fake module is registered under sys.modules rather than
    adding requests to this project's own requirements.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": 42}

    def fake_post(url, json=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    result = client.train_model({"a": 1})

    assert result == {"result": 42}
    assert calls[0]["url"] == "http://localhost:8000/train_model"
    assert calls[0]["json"] == {"a": 1}
    assert calls[0]["headers"] == {"X-API-Key": "notebook-to-api-dev-key"}


def test_sdk_pipeline_end_to_end_against_real_compiled_app(tmp_path):
    """Full real pipeline in a fresh subprocess (compile -> export-openapi
    -> export-sdk -> call the compiled app with the generated client),
    run out-of-process because openapi_exporter imports generated.app at
    module load time, which would otherwise get cached across tests in
    this same pytest process.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook
from backend.exporters.sdk_generator import generate_python_sdk
from fastapi.testclient import TestClient

compile_notebook({str(notebook_path)!r}, "generated")

# openapi_exporter imports generated.app at module load time (see the
# lazy-import comment in backend/cli.py), so it must not be imported
# until after compile_notebook has written a fresh generated/app.py --
# otherwise it can resolve a stale generated/app.py from elsewhere on
# sys.path instead of the one just compiled here.
from backend.exporters.openapi_exporter import export_openapi_schema
export_openapi_schema("generated/openapi.json")
generate_python_sdk("generated/openapi.json", "generated/sdk/python_client.py")

from generated.app import app

test_client = TestClient(app)

# `requests` is a dependency of the *generated* client, not of this repo's
# own venv, so a fake module is registered in sys.modules (routing calls
# through FastAPI's TestClient against the just-compiled app) instead of
# requiring the real package to be installed here.
import types

def fake_post(url, json=None, headers=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.post("/" + path, json=json, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

fake_requests = types.ModuleType("requests")
fake_requests.post = fake_post
sys.modules["requests"] = fake_requests

namespace = {{}}
exec(
    compile(open("generated/sdk/python_client.py").read(), "client.py", "exec"),
    namespace,
)

client = namespace["NotebookAPIClient"]("http://testserver")
result = client.add({{"a": 2, "b": 3}})
assert result == {{"result": 5}}, result
print("SDK_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SDK_E2E_OK" in proc.stdout
