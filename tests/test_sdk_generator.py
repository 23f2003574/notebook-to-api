import ast
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.exporters.sdk_generator import generate_python_sdk, generate_typescript_sdk

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


def test_generate_python_sdk_disambiguates_paths_that_collide_on_method_name(tmp_path):
    """Confirmed exploitable before this fix: a notebook function literally
    named "tasks_cleanup" (path "/tasks_cleanup") collides with the
    always-present built-in "/tasks/cleanup" route -- both sanitize to
    the same identifier "tasks_cleanup". The second `def` silently
    shadowed the first at class-body evaluation time, permanently hiding
    one endpoint from the generated SDK with no error anywhere.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/tasks/cleanup": {"post": {"operationId": "cleanup_tasks"}},
            "/tasks_cleanup": {"post": {"operationId": "tasks_cleanup"}},
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert source.count("def tasks_cleanup(self, payload: dict):") == 1
    assert source.count("def tasks_cleanup_2(self, payload: dict):") == 1


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


def test_generate_python_sdk_includes_task_polling_helpers(tmp_path):
    """A long-running notebook function's endpoint doesn't return its
    result directly -- it enqueues a background task and returns
    {"task_id": ..., "status": "processing"} (see LONG_RUNNING_KEYWORDS in
    api_generator.py). Before get_task/wait_for_task existed, the
    generated client had no way to actually retrieve that result short of
    a caller hand-writing their own polling loop against GET
    /tasks/{task_id}.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert "def get_task(self, task_id: str) -> dict:" in source
    assert "def wait_for_task(self, task_id: str" in source


def test_generate_python_sdk_get_task_sends_correct_request(tmp_path, monkeypatch):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_get(url, headers=None):
        calls.append({"url": url, "headers": headers})
        return FakeResponse({"status": "completed", "result": 42})

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    task = client.get_task("abc123")

    assert task == {"status": "completed", "result": 42}
    assert calls[0]["url"] == "http://localhost:8000/tasks/abc123"
    assert calls[0]["headers"] == {"X-API-Key": "notebook-to-api-dev-key"}


def test_generate_python_sdk_wait_for_task_polls_until_terminal_status(tmp_path, monkeypatch):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    responses = [
        {"status": "processing"},
        {"status": "processing"},
        {"status": "completed", "result": 42},
    ]
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_get(url, headers=None):
        calls.append(1)
        return FakeResponse(responses[len(calls) - 1])

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    task = client.wait_for_task("abc123", poll_interval=0, timeout=5)

    assert task == {"status": "completed", "result": 42}
    assert len(calls) == 3


def test_generate_python_sdk_wait_for_task_raises_timeout_error(tmp_path, monkeypatch):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "processing"}

    def fake_get(url, headers=None):
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")

    with pytest.raises(TimeoutError):
        client.wait_for_task("abc123", poll_interval=0, timeout=0)


def test_generate_typescript_sdk_produces_expected_structure(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "export class NotebookAPIClient {" in source
    assert "async train_model(payload: Record<string, unknown>): Promise<any> {" in source
    assert "async getTask(taskId: string): Promise<any> {" in source
    assert "async waitForTask(taskId: string" in source
    # Braces must balance -- a mismatch is the most common way hand-built
    # string-concatenated codegen produces invalid syntax.
    assert source.count("{") == source.count("}")


def test_generate_typescript_sdk_skips_non_post_endpoints(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {"post": {"operationId": "train_model"}},
            "/health": {"get": {"operationId": "health_check"}},
        },
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "async train_model(" in source
    assert "async health_check(" not in source


def test_generate_typescript_sdk_sends_api_key_header(tmp_path):
    """Mirrors test_generate_python_sdk_sends_api_key_header: the generated
    app rejects every request without an X-API-Key header, so the
    TypeScript client must default one too instead of silently 401ing.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert '"X-API-Key": this.apiKey' in source
    assert "notebook-to-api-dev-key" in source


def test_generate_typescript_sdk_method_name_handles_multi_segment_paths(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/tasks/cleanup": {"post": {"operationId": "cleanup_tasks"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "async tasks_cleanup(payload: Record<string, unknown>): Promise<any> {" in source


def test_generate_typescript_sdk_disambiguates_paths_that_collide_on_method_name(tmp_path):
    """Same collision as the Python SDK test, but for TypeScript: a
    duplicate `async tasks_cleanup(...)` class member is not just a
    silently-shadowed method (TS classes reject duplicate method names
    outright), it breaks `tsc` compilation of the generated client
    entirely.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/tasks/cleanup": {"post": {"operationId": "cleanup_tasks"}},
            "/tasks_cleanup": {"post": {"operationId": "tasks_cleanup"}},
        },
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert source.count("async tasks_cleanup(payload: Record<string, unknown>): Promise<any> {") == 1
    assert source.count("async tasks_cleanup_2(payload: Record<string, unknown>): Promise<any> {") == 1


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_client_sends_correct_request(tmp_path):
    """Load the generated client in a real Node.js process (mocking global
    fetch) to confirm the method actually builds the right URL/payload/
    header, rather than only checking the source text contains the right
    tokens -- same rigor as
    test_generate_python_sdk_client_sends_correct_request.

    Modern Node.js runs .ts files directly by stripping type annotations,
    so no separate `tsc`/bundler toolchain is required.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        globalThis.fetch = async (url, opts) => {{
          globalThis.__calls.push({{
            url,
            method: opts.method,
            apiKey: opts.headers["X-API-Key"],
            body: opts.body,
          }});
          return {{ ok: true, json: async () => ({{ result: 42 }}) }};
        }};
        globalThis.__calls = [];

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");
        const result = await client.train_model({{ a: 1 }});

        console.log(JSON.stringify({{ result, calls: globalThis.__calls }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output["result"] == {"result": 42}
    assert len(output["calls"]) == 1
    call = output["calls"][0]
    assert call["url"] == "http://localhost:8000/train_model"
    assert call["method"] == "POST"
    assert call["apiKey"] == "notebook-to-api-dev-key"
    assert json.loads(call["body"]) == {"a": 1}


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_wait_for_task_polls_until_completion(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        let callCount = 0;
        globalThis.fetch = async (url, opts) => {{
          callCount += 1;
          const status = callCount < 3 ? "processing" : "completed";
          return {{ ok: true, json: async () => ({{ status, result: 42 }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");
        const task = await client.waitForTask("abc123", {{ pollIntervalMs: 0, timeoutMs: 5000 }});

        console.log(JSON.stringify({{ task, callCount }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output["task"] == {"status": "completed", "result": 42}
    assert output["callCount"] == 3


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_wait_for_task_throws_on_timeout(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        globalThis.fetch = async (url, opts) => {{
          return {{ ok: true, json: async () => ({{ status: "processing" }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        try {{
          await client.waitForTask("abc123", {{ pollIntervalMs: 0, timeoutMs: 0 }});
          console.log(JSON.stringify({{ threw: false }}));
        }} catch (err) {{
          console.log(JSON.stringify({{ threw: true, message: err.message }}));
        }}
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output["threw"] is True
    assert "did not complete" in output["message"]


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

# openapi_exporter dynamically imports <package_name>.app inside
# export_openapi_schema (not at this module's own load time), so it must
# not be called until after compile_notebook has written a fresh
# generated/app.py -- otherwise it can resolve a stale generated/app.py
# from elsewhere on sys.path instead of the one just compiled here.
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


def test_export_openapi_schema_uses_the_freshly_compiled_app_for_custom_output_dir(
    tmp_path
):
    """Confirmed exploitable before this fix: export_openapi_schema always
    imported the fixed name "generated.app" regardless of what --output
    directory compilation actually used, so with any custom --output it
    silently exported the schema for whatever stale, unrelated app
    happened to already be importable as "generated.app" elsewhere on
    sys.path -- reproduced by compiling a uniquely-named function into a
    custom directory and finding it *missing* from the exported schema,
    with an unrelated stale endpoint present instead.

    Uses a distinctive, never-reused function name so a false pass can't
    be explained by another test's leftover "generated/" state.
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
                            "def distinctive_marker_endpoint_9f3a(q: int) -> int:\n"
                            "    return q\n"
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

from backend.compiler import compile_notebook, package_name_for_output_dir
from backend.exporters.openapi_exporter import export_openapi_schema

compile_notebook({str(notebook_path)!r}, "my_custom_api_dir")
export_openapi_schema(
    "my_custom_api_dir/openapi.json",
    package_name_for_output_dir("my_custom_api_dir"),
)

import json
schema = json.load(open("my_custom_api_dir/openapi.json"))
paths = schema.get("paths", {{}})
assert "/distinctive_marker_endpoint_9f3a" in paths, paths
print("CUSTOM_DIR_OPENAPI_EXPORT_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CUSTOM_DIR_OPENAPI_EXPORT_OK" in proc.stdout
