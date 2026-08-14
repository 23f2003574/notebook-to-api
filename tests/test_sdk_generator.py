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


@pytest.mark.parametrize(
    "colliding_name",
    [
        "get_task",
        "wait_for_task",
        "list_tasks",
        "delete_task",
        "delete_completed_tasks",
        "delete_failed_tasks",
    ],
)
def test_generate_python_sdk_disambiguates_a_path_colliding_with_a_hardcoded_client_method(
    tmp_path, colliding_name
):
    """Confirmed exploitable before this fix: get_task/wait_for_task/
    list_tasks/delete_task/delete_completed_tasks/delete_failed_tasks are
    emitted unconditionally, outside _build_method_names' per-path loop
    entirely -- so a notebook function landing on one of those exact
    names (none of them are reserved server-side; only the compiled app's
    own route names are, via RESERVED_INFRASTRUCTURE_NAMES in
    api_generator.py) produced a second, identically-named `def` that
    silently shadowed the real hardcoded method at class-body evaluation
    time, breaking task polling/management for every *other* endpoint
    that relies on it too (e.g. every *_and_wait companion calls
    self.wait_for_task(...) internally).
    """

    schema_path = _write_schema(
        tmp_path,
        {f"/{colliding_name}": {"post": {"operationId": colliding_name}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert source.count(f"def {colliding_name}(") == 1
    assert f"def {colliding_name}_2(self, payload: dict):" in source


def test_generate_python_sdk_wait_for_task_still_works_when_a_notebook_function_is_named_wait_for_task(
    tmp_path, monkeypatch
):
    """Behavioral, not just structural: the real hardcoded wait_for_task
    helper must still poll correctly -- not have been silently replaced
    by the notebook's own colliding /wait_for_task endpoint's method --
    even when such a collision exists in the same schema.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/wait_for_task": {"post": {"operationId": "wait_for_task"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    responses = [{"status": "processing"}, {"status": "completed", "result": 42}]
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


def test_generate_python_sdk_background_endpoint_gets_an_and_wait_companion(tmp_path):
    """Before this fix, a background endpoint's generated method looked
    identical to a synchronous one: it returned {"task_id": ...,
    "status": "processing"} immediately, with nothing in the generated
    client actually connecting that to get_task/wait_for_task -- a caller
    had no way to tell, from the client alone, that train_model(...)
    doesn't return the real result.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-async": True,
                }
            },
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert (
        "def train_model_and_wait(self, payload: dict, "
        "poll_interval: float = 1.0, timeout: float = 60.0) -> dict:"
        in source
    )
    assert "processing" in source.split("def train_model(")[1].split("def ")[0]


def test_generate_python_sdk_synchronous_endpoint_gets_no_and_wait_companion(
    tmp_path,
):

    schema_path = _write_schema(
        tmp_path,
        {"/add": {"post": {"operationId": "add"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "add_and_wait" not in source


def test_generate_python_sdk_and_wait_disambiguates_against_a_colliding_real_endpoint(
    tmp_path,
):
    """A real notebook function could easily be named
    "train_model_and_wait" -- the synthesized companion for the
    background "/train_model" endpoint must not silently collide with
    (and shadow) that real, separate endpoint's own method.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-async": True,
                }
            },
            "/train_model_and_wait": {
                "post": {
                    "operationId": "train_model_and_wait",
                    "x-notebook-to-api-async": True,
                }
            },
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    # The real "/train_model_and_wait" endpoint's own method.
    assert source.count("def train_model_and_wait(self, payload: dict):") == 1
    # The synthesized companion for "/train_model" was pushed to a
    # disambiguated name instead of colliding with it.
    assert (
        "def train_model_and_wait_2(self, payload: dict, "
        "poll_interval: float = 1.0, timeout: float = 60.0) -> dict:"
        in source
    )
    assert "def train_model_and_wait(self, payload: dict, " not in source


def test_generate_python_sdk_and_wait_submits_then_polls_to_completion(
    tmp_path, monkeypatch
):
    """Behavioral, not just structural: calling the companion method must
    actually submit the task and then poll it through to its finished
    result, in one call.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-async": True,
                }
            },
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    post_calls = []
    get_calls = []

    class FakePostResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"task_id": "abc123", "status": "processing"}

    class FakeGetResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_post(url, json=None, headers=None):
        post_calls.append({"url": url, "json": json})
        return FakePostResponse()

    get_responses = [
        {"status": "processing"},
        {"status": "completed", "result": 42},
    ]

    def fake_get(url, headers=None):
        get_calls.append(url)
        return FakeGetResponse(get_responses[len(get_calls) - 1])

    fake_requests = types.ModuleType("requests")
    fake_requests.post = fake_post
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    result = client.train_model_and_wait({"epochs": 5}, poll_interval=0, timeout=5)

    assert result == {"status": "completed", "result": 42}
    assert post_calls == [
        {"url": "http://localhost:8000/train_model", "json": {"epochs": 5}}
    ]
    assert get_calls == [
        "http://localhost:8000/tasks/abc123",
        "http://localhost:8000/tasks/abc123",
    ]


def test_generate_python_sdk_includes_task_management_helpers(tmp_path):
    """Every compiled app guarantees GET /tasks, DELETE /tasks/{task_id},
    DELETE /tasks/completed, and DELETE /tasks/failed (see
    RESERVED_INFRASTRUCTURE_NAMES in api_generator.py), but the per-path
    loop only emits a method for POST paths -- before this, the generated
    client had get_task/wait_for_task for polling a single already-known
    task, but no way to see what else is running or clear out finished
    tasks.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert "def list_tasks(self) -> dict:" in source
    assert "def delete_task(self, task_id: str) -> dict:" in source
    assert "def delete_completed_tasks(self) -> dict:" in source
    assert "def delete_failed_tasks(self) -> dict:" in source


def test_generate_python_sdk_list_tasks_sends_correct_request(tmp_path, monkeypatch):

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
            return {"active_tasks": 0, "tasks": {}}

    def fake_get(url, headers=None):
        calls.append({"url": url, "headers": headers})
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    result = client.list_tasks()

    assert result == {"active_tasks": 0, "tasks": {}}
    assert calls == [
        {
            "url": "http://localhost:8000/tasks",
            "headers": {"X-API-Key": "notebook-to-api-dev-key"},
        }
    ]


def test_generate_python_sdk_delete_task_sends_correct_request(tmp_path, monkeypatch):

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
            return {"message": "Task deleted", "task_id": "abc123"}

    def fake_delete(url, headers=None):
        calls.append({"url": url, "headers": headers})
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.delete = fake_delete
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    result = client.delete_task("abc123")

    assert result == {"message": "Task deleted", "task_id": "abc123"}
    assert calls == [
        {
            "url": "http://localhost:8000/tasks/abc123",
            "headers": {"X-API-Key": "notebook-to-api-dev-key"},
        }
    ]


def test_generate_python_sdk_delete_completed_and_failed_tasks_send_correct_requests(
    tmp_path, monkeypatch
):

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
            return {"deleted": 0}

    def fake_delete(url, headers=None):
        calls.append(url)
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.delete = fake_delete
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    client.delete_completed_tasks()
    client.delete_failed_tasks()

    assert calls == [
        "http://localhost:8000/tasks/completed",
        "http://localhost:8000/tasks/failed",
    ]


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


@pytest.mark.parametrize(
    "colliding_name",
    [
        "getTask",
        "waitForTask",
        "listTasks",
        "deleteTask",
        "deleteCompletedTasks",
        "deleteFailedTasks",
    ],
)
def test_generate_typescript_sdk_disambiguates_a_path_colliding_with_a_hardcoded_client_method(
    tmp_path, colliding_name
):
    """Mirrors
    test_generate_python_sdk_disambiguates_a_path_colliding_with_a_hardcoded_client_method
    for the TypeScript client's own hardcoded getTask/waitForTask/
    listTasks/deleteTask/deleteCompletedTasks/deleteFailedTasks methods.
    """

    schema_path = _write_schema(
        tmp_path,
        {f"/{colliding_name}": {"post": {"operationId": colliding_name}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert source.count(f"async {colliding_name}(") == 1
    assert (
        f"async {colliding_name}_2(payload: Record<string, unknown>): Promise<any> {{"
        in source
    )


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


def test_generate_typescript_sdk_background_endpoint_gets_an_and_wait_companion(
    tmp_path,
):
    """Mirrors test_generate_python_sdk_background_endpoint_gets_an_and_wait_companion
    for the TypeScript client.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-async": True,
                }
            },
        },
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    source = client_path.read_text(encoding="utf-8")

    assert (
        "async train_model_and_wait(payload: Record<string, unknown>, "
        "options: { pollIntervalMs?: number; timeoutMs?: number } = {}): "
        "Promise<any> {" in source
    )


def test_generate_typescript_sdk_synchronous_endpoint_gets_no_and_wait_companion(
    tmp_path,
):

    schema_path = _write_schema(
        tmp_path,
        {"/add": {"post": {"operationId": "add"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    assert "add_and_wait" not in client_path.read_text(encoding="utf-8")


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_and_wait_submits_then_polls_to_completion(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-async": True,
                }
            },
        },
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        const calls = [];
        let getCount = 0;
        globalThis.fetch = async (url, opts) => {{
          calls.push({{ url, method: opts && opts.method }});
          if (opts && opts.method === "POST") {{
            return {{ ok: true, json: async () => ({{ task_id: "abc123", status: "processing" }}) }};
          }}
          getCount += 1;
          const status = getCount < 2 ? "processing" : "completed";
          return {{ ok: true, json: async () => ({{ status, result: 42 }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");
        const result = await client.train_model_and_wait(
          {{ epochs: 5 }}, {{ pollIntervalMs: 0, timeoutMs: 5000 }}
        );

        console.log(JSON.stringify({{ result, calls }}));
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
    assert output["result"] == {"status": "completed", "result": 42}
    assert output["calls"][0] == {
        "url": "http://localhost:8000/train_model",
        "method": "POST",
    }
    assert all(
        call["url"] == "http://localhost:8000/tasks/abc123"
        for call in output["calls"][1:]
    )


def test_generate_typescript_sdk_includes_task_management_helpers(tmp_path):
    """Mirrors test_generate_python_sdk_includes_task_management_helpers
    for the TypeScript client."""

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    source = client_path.read_text(encoding="utf-8")

    assert "async listTasks(): Promise<any> {" in source
    assert "async deleteTask(taskId: string): Promise<any> {" in source
    assert "async deleteCompletedTasks(): Promise<any> {" in source
    assert "async deleteFailedTasks(): Promise<any> {" in source


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_task_management_methods_send_correct_requests(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        const calls = [];
        globalThis.fetch = async (url, opts) => {{
          calls.push({{ url, method: (opts && opts.method) || "GET" }});
          return {{ ok: true, json: async () => ({{}}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        await client.listTasks();
        await client.deleteTask("abc123");
        await client.deleteCompletedTasks();
        await client.deleteFailedTasks();

        console.log(JSON.stringify({{ calls }}));
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
    assert output["calls"] == [
        {"url": "http://localhost:8000/tasks", "method": "GET"},
        {"url": "http://localhost:8000/tasks/abc123", "method": "DELETE"},
        {"url": "http://localhost:8000/tasks/completed", "method": "DELETE"},
        {"url": "http://localhost:8000/tasks/failed", "method": "DELETE"},
    ]


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


def test_sdk_pipeline_and_wait_end_to_end_against_a_real_background_endpoint(tmp_path):
    """The same full real pipeline as
    test_sdk_pipeline_end_to_end_against_real_compiled_app, but for a
    background/task_id-based endpoint (see LONG_RUNNING_KEYWORDS in
    api_generator.py): confirms the generated *_and_wait companion
    method actually submits the task to the real compiled app and polls
    it through to its real, finished result in one call -- not just that
    the generated source text looks right.
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
                            "def train_model(epochs: int) -> str:\n"
                            "    return f'trained for {epochs} epochs'\n"
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

from backend.exporters.openapi_exporter import export_openapi_schema
export_openapi_schema("generated/openapi.json")
generate_python_sdk("generated/openapi.json", "generated/sdk/python_client.py")

from generated.app import app

test_client = TestClient(app)

import types

def fake_post(url, json=None, headers=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.post("/" + path, json=json, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

def fake_get(url, headers=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.get("/" + path, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

fake_requests = types.ModuleType("requests")
fake_requests.post = fake_post
fake_requests.get = fake_get
sys.modules["requests"] = fake_requests

namespace = {{}}
exec(
    compile(open("generated/sdk/python_client.py").read(), "client.py", "exec"),
    namespace,
)

client = namespace["NotebookAPIClient"]("http://testserver")

# The base method alone must still return the raw task descriptor, not
# the real result.
submitted = client.train_model({{"epochs": 3}})
assert submitted["status"] == "processing", submitted
assert "task_id" in submitted, submitted

# The companion method submits and polls through to the real result.
finished = client.train_model_and_wait(
    {{"epochs": 3}}, poll_interval=0.01, timeout=5
)
assert finished["status"] == "completed", finished
assert finished["result"] == "trained for 3 epochs", finished

print("SDK_AND_WAIT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SDK_AND_WAIT_E2E_OK" in proc.stdout


def test_sdk_pipeline_task_management_end_to_end_against_a_real_compiled_app(tmp_path):
    """Same full real pipeline as
    test_sdk_pipeline_end_to_end_against_real_compiled_app, but exercising
    list_tasks/delete_task/delete_completed_tasks/delete_failed_tasks
    against the real compiled app's task registry -- confirms they
    actually see and remove tasks created by a real background endpoint,
    not just that the generated source text looks right.
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
                            "def train_model(epochs: int) -> str:\n"
                            "    return f'trained for {epochs} epochs'\n"
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

from backend.exporters.openapi_exporter import export_openapi_schema
export_openapi_schema("generated/openapi.json")
generate_python_sdk("generated/openapi.json", "generated/sdk/python_client.py")

from generated.app import app

test_client = TestClient(app)

import types

def fake_post(url, json=None, headers=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.post("/" + path, json=json, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

def fake_get(url, headers=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.get("/" + path, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

def fake_delete(url, headers=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.delete("/" + path, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

fake_requests = types.ModuleType("requests")
fake_requests.post = fake_post
fake_requests.get = fake_get
fake_requests.delete = fake_delete
sys.modules["requests"] = fake_requests

namespace = {{}}
exec(
    compile(open("generated/sdk/python_client.py").read(), "client.py", "exec"),
    namespace,
)

client = namespace["NotebookAPIClient"]("http://testserver")

submitted = client.train_model({{"epochs": 3}})
task_id = submitted["task_id"]

# list_tasks sees the just-submitted task.
listing = client.list_tasks()
assert task_id in listing["tasks"], listing

finished = client.wait_for_task(task_id, poll_interval=0.01, timeout=5)
assert finished["status"] == "completed", finished

# delete_completed_tasks clears it out.
deleted = client.delete_completed_tasks()
assert deleted["deleted"] == 1, deleted
assert task_id not in client.list_tasks()["tasks"]

# delete_task removes a specific task by id; delete_failed_tasks is a
# no-op with nothing failed.
second = client.train_model({{"epochs": 1}})
second_id = second["task_id"]
client.wait_for_task(second_id, poll_interval=0.01, timeout=5)
client.delete_task(second_id)
assert second_id not in client.list_tasks()["tasks"]

no_op = client.delete_failed_tasks()
assert no_op["deleted"] == 0, no_op

print("SDK_TASK_MANAGEMENT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SDK_TASK_MANAGEMENT_E2E_OK" in proc.stdout


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
