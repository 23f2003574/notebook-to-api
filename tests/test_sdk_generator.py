import ast
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.exporters.sdk_generator import (
    generate_python_sdk,
    generate_typescript_sdk,
    _pascal_case,
    _python_type_to_typescript,
    _json_schema_type_to_typescript,
)

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


def _exec_generated_client(output_path, monkeypatch):
    """Load a generated Python client module in isolation, stubbing
    `requests` (a dependency of the *generated* client, not of this repo)
    under sys.modules the same way test_generate_python_sdk_client_sends_
    correct_request does, so importing the generated source itself (not
    just parsing/compiling it) doesn't require requests to actually be
    installed in this test environment.
    """
    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda *a, **k: None
    fake_requests.post = lambda *a, **k: None
    fake_requests.delete = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(
        compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"),
        namespace,
    )
    return namespace["NotebookAPIClient"]


def test_generate_python_sdk_method_docstring_includes_the_operations_description(
    tmp_path, monkeypatch
):
    """Before this, a generated client method's docstring was pure
    hardcoded boilerplate ("Call the `/path` endpoint with JSON
    payload.") with zero connection to what the endpoint actually does --
    even though the OpenAPI schema being read right here already carries
    real documentation for it (a notebook function's own docstring, or
    this tool's own auto-generated fallback -- see api_generator.py).
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "description": "Train the classifier and return its accuracy.",
                }
            }
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")
    ast.parse(source)

    assert "Train the classifier and return its accuracy." in source

    client_cls = _exec_generated_client(output_path, monkeypatch)
    assert "Train the classifier and return its accuracy." in client_cls.train_model.__doc__


def test_generate_python_sdk_method_docstring_falls_back_without_a_description(
    tmp_path, monkeypatch
):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")
    ast.parse(source)

    client_cls = _exec_generated_client(output_path, monkeypatch)
    assert client_cls.train_model.__doc__ == (
        "Call the `/train_model` endpoint with JSON payload."
    )


def test_generate_python_sdk_method_docstring_survives_a_description_with_quotes_and_newlines(
    tmp_path, monkeypatch
):
    """Confirmed exploitable before description was repr()'d into the
    docstring statement rather than embedded in a hand-written triple-
    quoted literal: a description containing a triple-quote sequence, a
    lone double quote, or a backslash -- all legitimate content for a
    notebook author's own docstring, or FastAPI's own escaping of an
    unrelated field -- would close the docstring literal early and
    corrupt the rest of the generated client into a SyntaxError, the
    exact hazard e91b1fa already fixed for the compiled app itself.
    """

    tricky_description = 'Say "hi", use a \\backslash\\, and go\nmultiline. Even a triple quote: """ survives.'

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "description": tricky_description,
                }
            }
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")
    ast.parse(source)
    compile(source, str(output_path), "exec")

    client_cls = _exec_generated_client(output_path, monkeypatch)
    assert tricky_description in client_cls.train_model.__doc__


def test_generate_python_sdk_and_wait_docstring_includes_the_operations_description(
    tmp_path, monkeypatch
):

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "description": "Train the classifier and return its accuracy.",
                    "x-notebook-to-api-async": True,
                }
            }
        },
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    client_cls = _exec_generated_client(output_path, monkeypatch)
    assert (
        "Train the classifier and return its accuracy."
        in client_cls.train_model_and_wait.__doc__
    )


def test_generate_python_sdk_reports_a_clean_error_for_invalid_json(tmp_path):
    """Before _load_openapi_schema existed, a bare json.load(f) crashed
    with json.JSONDecodeError's raw, low-level message ("Expecting
    value: line 1 column 1 (char 0)") for any --openapi file that isn't
    valid JSON, with no indication of why.
    """

    schema_path = tmp_path / "openapi.json"
    schema_path.write_text("not json at all", encoding="utf-8")
    output_path = tmp_path / "client.py"

    with pytest.raises(ValueError, match="not a valid OpenAPI JSON schema"):
        generate_python_sdk(str(schema_path), str(output_path))


def test_generate_python_sdk_hints_at_a_yaml_export_for_a_yaml_extension(tmp_path):
    """The most likely real-world cause of a JSON decode failure here is
    one this tool itself creates: POST /api/export-openapi and `export-
    openapi --format yaml` write a YAML file this function was never
    able to read -- a caller pointing export-sdk at that exact file (both
    commands read/write the same output directory by default) deserves a
    hint about what actually went wrong, not just a bare parse error.
    """

    schema_path = tmp_path / "openapi.yaml"
    schema_path.write_text("paths:\n  /add:\n    post: {}\n", encoding="utf-8")
    output_path = tmp_path / "client.py"

    with pytest.raises(ValueError, match="export-openapi --format yaml"):
        generate_python_sdk(str(schema_path), str(output_path))


def test_generate_typescript_sdk_reports_a_clean_error_for_invalid_json(tmp_path):

    schema_path = tmp_path / "openapi.json"
    schema_path.write_text("not json at all", encoding="utf-8")
    output_path = tmp_path / "client.ts"

    with pytest.raises(ValueError, match="not a valid OpenAPI JSON schema"):
        generate_typescript_sdk(str(schema_path), str(output_path))


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


def test_generate_python_sdk_constructor_accepts_a_configurable_timeout(tmp_path):
    """`requests` has no default socket timeout of its own -- a call with
    none set can hang indefinitely on a server that accepts the
    connection but never responds. Before this, the generated client
    never passed `timeout=` to any of its requests.* calls at all, and
    its constructor had no way to configure one either.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "timeout: float = 30.0" in source
    assert "self.timeout = timeout" in source
    # Every requests.* call this client makes must actually use it: the 6
    # hardcoded task methods (get_task, list_tasks, delete_task,
    # delete_completed_tasks, delete_failed_tasks, plus the single
    # "/train_model" path this test's own schema declares -- wait_for_task
    # makes no request of its own, it only calls self.get_task), plus the
    # 9 hardcoded health/ready/info/config/metrics/uptime/auth_status/
    # auth_info/auth_validate methods.
    assert source.count("timeout=self.timeout") == 15


def test_generate_python_sdk_uses_the_configured_timeout_for_a_request(
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
            return {"result": 42}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(timeout)
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000", timeout=5.0)
    client.train_model({"a": 1})

    assert calls == [5.0]


def test_generate_typescript_sdk_constructor_accepts_a_configurable_timeout(
    tmp_path,
):
    """Mirrors
    test_generate_python_sdk_constructor_accepts_a_configurable_timeout
    for the TypeScript client -- fetch() has no default timeout of its
    own either, and none of the generated methods passed an AbortSignal
    at all before this.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "timeoutMs?: number;" in source
    assert "this.timeoutMs = options.timeoutMs ?? 30000;" in source
    # Every fetch() call this client makes must actually use it: the 6
    # hardcoded task methods (getTask, listTasks, deleteTask,
    # deleteCompletedTasks, deleteFailedTasks, plus the single shared
    # private `request()` helper every POST-path method -- "/train_model"
    # in this test's own schema -- funnels through, regardless of how many
    # such paths exist), plus the 9 hardcoded health/ready/info/config/
    # metrics/uptime/authStatus/authInfo/authValidate methods.
    assert source.count("signal: AbortSignal.timeout(this.timeoutMs),") == 15


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


@pytest.mark.parametrize(
    "colliding_name", ["base_url", "api_key", "timeout", "max_retries", "backoff_factor"]
)
def test_generate_python_sdk_disambiguates_a_path_colliding_with_an_instance_attribute(
    tmp_path, colliding_name
):
    """Confirmed exploitable before this fix: base_url/api_key/timeout
    are the client's own __init__-set *instance attributes* (every other
    method reads self.base_url/self.api_key/self.timeout for exactly
    that reason) -- not just names an unrelated method happened to
    share. A notebook path sanitizing to one of these compiled into a
    same-named client *method* fine, but an instance attribute set in
    __init__ shadows a class-level method of the same name on attribute
    lookup: `self.base_url` from then on resolves to the string, not the
    method, so calling client.base_url(...) fails with "'str' object is
    not callable", with nothing about the method definition itself
    signaling why.
    """

    schema_path = _write_schema(
        tmp_path,
        {f"/{colliding_name}": {"post": {"operationId": colliding_name}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert f"def {colliding_name}_2(self, payload: dict):" in source


@pytest.mark.parametrize(
    "colliding_name", ["baseUrl", "apiKey", "timeoutMs", "maxRetries", "backoffFactor"]
)
def test_generate_typescript_sdk_disambiguates_a_path_colliding_with_an_instance_field(
    tmp_path, colliding_name
):
    """Mirrors
    test_generate_python_sdk_disambiguates_a_path_colliding_with_an_instance_attribute
    for the TypeScript client: baseUrl/apiKey/timeoutMs are its own
    private instance fields, and a class can't declare a field and a
    method under the same identifier at all -- "Duplicate identifier" at
    TypeScript compile time.
    """

    schema_path = _write_schema(
        tmp_path,
        {f"/{colliding_name}": {"post": {"operationId": colliding_name}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert f"async {colliding_name}_2(" in source


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

    def fake_get(url, headers=None, timeout=None):
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

    def fake_post(url, json=None, headers=None, timeout=None):
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

    def fake_get(url, headers=None, timeout=None):
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

    def fake_get(url, headers=None, timeout=None):
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

    def fake_get(url, headers=None, timeout=None):
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")

    with pytest.raises(TimeoutError):
        client.wait_for_task("abc123", poll_interval=0, timeout=0)


def test_generate_python_sdk_wait_for_task_raises_for_a_not_found_task(tmp_path, monkeypatch):
    """Before the generated app's GET /tasks/{task_id} was fixed to return
    a real 404 for an unknown/evicted task_id (generator/api_generator.py)
    instead of a 200 with {"error": "Task not found"}, this exact
    get_task/wait_for_task contract -- relying on
    requests.Response.raise_for_status() to signal failure -- silently
    did nothing: raise_for_status() only raises for a non-2xx status, and
    wait_for_task's own `task.get('status') != 'processing'` check saw a
    missing 'status' key on the error body and treated it as a finished
    task, handing the error dict back to the caller as if it were the
    real result. Simulating the now-fixed 404 here confirms get_task and
    wait_for_task actually propagate that failure instead of masking it --
    and, now that wait_for_task retries a *transient* failure (429/502/
    503/504) instead of raising immediately (see
    test_generate_python_sdk_wait_for_task_retries_transient_errors_
    before_succeeding below), that a 404 specifically -- carried on
    FakeHTTPError's own `.response.status_code`, mirroring how a real
    requests.exceptions.HTTPError raised by raise_for_status() always
    carries the original response on `.response` -- is still treated as
    permanent and propagates on the very first attempt, not retried.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    class FakeResponse:
        status_code = 404

        def raise_for_status(self):
            raise FakeHTTPError("404 Client Error: Not Found", response=self)

        def json(self):
            return {"detail": "Task abc123 not found"}

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(1)
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")

    with pytest.raises(FakeHTTPError):
        client.get_task("abc123")

    calls.clear()

    with pytest.raises(FakeHTTPError):
        client.wait_for_task("abc123", poll_interval=0, timeout=5)

    assert len(calls) == 1, "a 404 must propagate on the first attempt, not be retried"


def test_generate_python_sdk_wait_for_task_retries_transient_errors_before_succeeding(
    tmp_path, monkeypatch
):
    """Confirmed exploitable before this fix: wait_for_task's whole
    contract, per its own docstring, is "poll until finished or timeout
    elapses" -- but a single transient failure while polling (a 429 from
    the generated app's own rate limiter, a 503 from a server mid-
    restart, or a bare connection error) propagated straight out of the
    loop and aborted the wait immediately, instead of being retried like
    any other still-processing task. Exercises both shapes: an
    HTTPError-style failure carrying a transient `.response.status_code`,
    and a raw connection-level failure carrying no `.response` at all.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    class FakeConnectionError(Exception):
        pass

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise FakeHTTPError(
                    f"{self.status_code} error", response=self
                )

        def json(self):
            return self._payload

    # First call: a 503 (transient HTTP status). Second call: a bare
    # connection error (no `.response` at all -- the real
    # requests.exceptions.ConnectionError shape). Third call: succeeds.
    responses = [
        FakeResponse(503),
        FakeConnectionError("connection reset"),
        FakeResponse(200, {"status": "completed", "result": 42}),
    ]
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(1)
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    task = client.wait_for_task("abc123", poll_interval=0, timeout=5)

    assert task == {"status": "completed", "result": 42}
    assert len(calls) == 3


def test_generate_python_sdk_wait_for_task_still_times_out_when_transient_errors_persist(
    tmp_path, monkeypatch
):
    """A transient failure must still respect `timeout` -- retrying
    forever would defeat wait_for_task's own timeout contract just as
    badly as never retrying at all.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    class FakeResponse:
        status_code = 503

        def raise_for_status(self):
            raise FakeHTTPError("503 error", response=self)

    def fake_get(url, headers=None, timeout=None):
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

    def fake_post(url, json=None, headers=None, timeout=None):
        post_calls.append({"url": url, "json": json})
        return FakePostResponse()

    get_responses = [
        {"status": "processing"},
        {"status": "completed", "result": 42},
    ]

    def fake_get(url, headers=None, timeout=None):
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
    assert (
        "def list_tasks(\n"
        "        self, status: str = None, limit: int = None, "
        "offset: int = None,\n"
        "    ) -> dict:"
    ) in source
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

    def fake_get(url, headers=None, timeout=None, params=None):
        calls.append({"url": url, "headers": headers, "params": params})
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
            "params": {},
        }
    ]


def test_generate_python_sdk_list_tasks_forwards_status_limit_offset(tmp_path, monkeypatch):
    """Confirmed exploitable before this fix: list_tasks() accepted no
    arguments at all and always sent a bare `/tasks` request, so the
    status/limit/offset filtering the generated server's own GET /tasks
    accepts (see api_generator.py) was entirely unreachable through this
    client -- a caller had to bypass it and issue the raw HTTP request
    themselves to use it.
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
            return {"tasks": {}}

    def fake_get(url, headers=None, timeout=None, params=None):
        calls.append(params)
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")

    client.list_tasks(status="failed")
    client.list_tasks(limit=10, offset=20)
    client.list_tasks(status="completed", limit=5, offset=0)

    assert calls == [
        {"status": "failed"},
        {"limit": 10, "offset": 20},
        {"status": "completed", "limit": 5, "offset": 0},
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

    def fake_delete(url, headers=None, timeout=None):
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

    def fake_delete(url, headers=None, timeout=None):
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


def test_generate_python_sdk_includes_infrastructure_helpers(tmp_path):
    """Every compiled app guarantees GET /health, /ready, /info, /config,
    /metrics, /uptime, /auth/status, /auth/info, and /auth/validate (see
    RESERVED_INFRASTRUCTURE_NAMES in api_generator.py), but the per-path
    loop only emits a method for POST paths -- before this, a caller
    wanting a liveness/readiness probe, service info, its own runtime
    config, request metrics, or auth configuration through the generated
    client had no way to do it short of hand-writing the exact same
    requests.get call get_task already demonstrates this client knows how
    to make. "config" (GET /config, added in Commit #12) is confirmed to
    have been missed here even though the server-side route already
    existed -- the exact drift class this reserved-names mechanism
    guards the server side against, just never itself checked against
    the client side.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert "def health(self) -> dict:" in source
    assert "def ready(self) -> dict:" in source
    assert "def info(self) -> dict:" in source
    assert "def config(self) -> dict:" in source
    assert "def metrics(self) -> dict:" in source
    assert "def uptime(self) -> dict:" in source
    assert "def auth_status(self) -> dict:" in source
    assert "def auth_info(self) -> dict:" in source
    assert "def auth_validate(self) -> dict:" in source


def test_generate_python_sdk_infrastructure_helpers_send_correct_requests(
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
            return {"status": "ok"}

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"), namespace)

    client = namespace["NotebookAPIClient"]("http://localhost:8000", api_key="secret-key")

    assert client.health() == {"status": "ok"}
    assert client.ready() == {"status": "ok"}
    assert client.info() == {"status": "ok"}
    assert client.config() == {"status": "ok"}
    assert client.metrics() == {"status": "ok"}
    assert client.uptime() == {"status": "ok"}
    assert client.auth_status() == {"status": "ok"}
    assert client.auth_info() == {"status": "ok"}
    assert client.auth_validate() == {"status": "ok"}

    assert calls == [
        {"url": "http://localhost:8000/health", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/ready", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/info", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/config", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/metrics", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/uptime", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/auth/status", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/auth/info", "headers": {"X-API-Key": "secret-key"}},
        {"url": "http://localhost:8000/auth/validate", "headers": {"X-API-Key": "secret-key"}},
    ]


def test_generate_python_sdk_infrastructure_helper_names_take_priority_over_a_colliding_path(
    tmp_path,
):
    """Same collision hazard PYTHON_RESERVED_CLIENT_METHOD_NAMES' own
    docstring already documents for get_task/wait_for_task/etc. -- a
    notebook path sanitizing to "health" (e.g. "/health") would otherwise
    silently redefine (Python) the real infrastructure method. This can't
    actually happen server-side (RESERVED_INFRASTRUCTURE_NAMES already
    blocks a notebook function literally named "health_check" et al. from
    compiling at all), but "health" itself is not in that server-side
    set, so nothing before this stopped it at the SDK-generation layer.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/health": {"post": {"operationId": "health"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert "def health(self) -> dict:" in source
    assert "def health_2(self, payload: dict):" in source


def _exec_python_client_with_fake_requests(output_path, monkeypatch, **fake_fns):
    """Load a generated Python client with `fake_fns` (e.g. get=..., post=...)
    installed on a fake `requests` module, mirroring the pattern every other
    Python-client test in this file already uses.
    """
    fake_requests = types.ModuleType("requests")
    for name, fn in fake_fns.items():
        setattr(fake_requests, name, fn)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(
        compile(output_path.read_text(encoding="utf-8"), str(output_path), "exec"),
        namespace,
    )
    return namespace


def test_generate_python_sdk_retries_a_transient_failure_before_succeeding(
    tmp_path, monkeypatch
):
    """Confirmed missing before this feature: only wait_for_task's own
    polling loop ever retried a transient failure -- every other call
    this client makes (submitting a notebook function, list_tasks/
    delete_task/..., health/ready/info/config/metrics/uptime/auth_*)
    raised immediately on a single 429/502/503/504, even though the
    generated server side already reports exactly when a caller should
    retry (Retry-After/X-RateLimit-Reset).
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, response):
            super().__init__(f"{response.status_code} error")
            self.response = response

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise FakeHTTPError(self)

        def json(self):
            return self._payload

    responses = [
        FakeResponse(503),
        FakeResponse(503),
        FakeResponse(200, {"result": 42}),
    ]
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return responses[len(calls) - 1]

    namespace = _exec_python_client_with_fake_requests(
        output_path, monkeypatch, post=fake_post
    )

    client = namespace["NotebookAPIClient"](
        "http://localhost:8000", backoff_factor=0
    )
    result = client.train_model({"a": 1})

    assert result == {"result": 42}
    assert len(calls) == 3


def test_generate_python_sdk_does_not_retry_a_non_transient_failure(
    tmp_path, monkeypatch
):
    """A genuine 404/401/... must still raise on the very first attempt --
    retrying it would just waste time reproducing the identical failure.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, response):
            super().__init__("404 error")
            self.response = response

    class FakeResponse:
        status_code = 404
        headers = {}

        def raise_for_status(self):
            raise FakeHTTPError(self)

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return FakeResponse()

    namespace = _exec_python_client_with_fake_requests(
        output_path, monkeypatch, post=fake_post
    )

    client = namespace["NotebookAPIClient"]("http://localhost:8000")

    with pytest.raises(FakeHTTPError):
        client.train_model({})

    assert len(calls) == 1


def test_generate_python_sdk_gives_up_after_max_retries(tmp_path, monkeypatch):
    """A transient failure that never clears up must still eventually
    surface to the caller, bounded by max_retries -- retrying forever
    would just hang the caller instead of ever reporting the failure.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, response):
            super().__init__("503 error")
            self.response = response

    class FakeResponse:
        status_code = 503
        headers = {}

        def raise_for_status(self):
            raise FakeHTTPError(self)

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return FakeResponse()

    namespace = _exec_python_client_with_fake_requests(
        output_path, monkeypatch, post=fake_post
    )

    client = namespace["NotebookAPIClient"](
        "http://localhost:8000", max_retries=2, backoff_factor=0
    )

    with pytest.raises(FakeHTTPError):
        client.train_model({})

    assert len(calls) == 3, "1 initial attempt + 2 retries"


def test_generate_python_sdk_max_retries_zero_disables_retry(tmp_path, monkeypatch):
    """max_retries=0 must reproduce the exact pre-feature behavior: raise
    on the very first transient failure, never retry at all.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, response):
            super().__init__("503 error")
            self.response = response

    class FakeResponse:
        status_code = 503
        headers = {}

        def raise_for_status(self):
            raise FakeHTTPError(self)

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return FakeResponse()

    namespace = _exec_python_client_with_fake_requests(
        output_path, monkeypatch, post=fake_post
    )

    client = namespace["NotebookAPIClient"]("http://localhost:8000", max_retries=0)

    with pytest.raises(FakeHTTPError):
        client.train_model({})

    assert len(calls) == 1


def test_generate_python_sdk_retry_honors_retry_after_header(tmp_path, monkeypatch):
    """A 429's own Retry-After header (the generated server always sends
    one -- see _enforce_rate_limit in api_generator.py) must drive the
    actual wait, not just the generic exponential backoff.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, response):
            super().__init__("429 error")
            self.response = response

    class FakeResponse:
        def __init__(self, status_code, headers=None, payload=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise FakeHTTPError(self)

        def json(self):
            return self._payload

    responses = [
        FakeResponse(429, headers={"Retry-After": "7"}),
        FakeResponse(200, payload={"result": 1}),
    ]
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return responses[len(calls) - 1]

    namespace = _exec_python_client_with_fake_requests(
        output_path, monkeypatch, post=fake_post
    )

    sleeps = []
    namespace["time"].sleep = lambda seconds: sleeps.append(seconds)

    client = namespace["NotebookAPIClient"]("http://localhost:8000")
    result = client.train_model({})

    assert result == {"result": 1}
    assert sleeps == [7.0]


def test_generate_python_sdk_list_tasks_and_infra_methods_also_retry(
    tmp_path, monkeypatch
):
    """The retry/backoff addition isn't limited to per-function POST
    endpoints -- list_tasks/delete_task/... and the hardcoded health/
    ready/info/... GET methods route through the identical helper.
    """

    schema_path = _write_schema(tmp_path, {})
    output_path = tmp_path / "client.py"

    generate_python_sdk(str(schema_path), str(output_path))

    class FakeHTTPError(Exception):
        def __init__(self, response):
            super().__init__("503 error")
            self.response = response

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.headers = {}
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise FakeHTTPError(self)

        def json(self):
            return self._payload

    responses = [
        FakeResponse(503),
        FakeResponse(200, {"status": "ok"}),
    ]
    calls = []

    def fake_get(url, headers=None, timeout=None, params=None):
        calls.append(1)
        return responses[len(calls) - 1]

    namespace = _exec_python_client_with_fake_requests(
        output_path, monkeypatch, get=fake_get
    )

    client = namespace["NotebookAPIClient"]("http://localhost:8000", backoff_factor=0)

    assert client.health() == {"status": "ok"}
    assert len(calls) == 2


def test_generate_typescript_sdk_produces_expected_structure(tmp_path):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "export class NotebookAPIClient {" in source
    assert "async train_model(payload: TrainModelRequest): Promise<TrainModelResponse> {" in source
    assert "async getTask(taskId: string): Promise<any> {" in source
    assert "async waitForTask(taskId: string" in source
    # Braces must balance -- a mismatch is the most common way hand-built
    # string-concatenated codegen produces invalid syntax.
    assert source.count("{") == source.count("}")


def test_generate_typescript_sdk_method_jsdoc_includes_the_operations_description(
    tmp_path,
):
    """Mirrors
    test_generate_python_sdk_method_docstring_includes_the_operations_description
    for the TypeScript client -- before this, a synchronous method got no
    doc comment at all, and a background method's JSDoc was pure
    hardcoded boilerplate, either way with zero connection to what the
    endpoint actually does.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "description": "Train the classifier and return its accuracy.",
                }
            }
        },
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "/**" in source
    assert " * Train the classifier and return its accuracy." in source
    assert (
        "async train_model(payload: TrainModelRequest): Promise<TrainModelResponse> {"
        in source
    )
    assert source.count("{") == source.count("}")


def test_generate_typescript_sdk_method_jsdoc_falls_back_without_a_description(
    tmp_path,
):

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert " * Calls the `/train_model` endpoint with JSON payload." in source


def test_generate_typescript_sdk_jsdoc_neutralizes_a_terminator_sequence_in_the_description(
    tmp_path,
):
    """Confirmed exploitable before this: unlike Python's repr(), a JS/TS
    block comment has no escape mechanism at all -- a literal "*/" inside
    a description (a notebook author's own docstring can legitimately
    contain one, e.g. documenting a regex or a comment) would close the
    JSDoc comment early, corrupting whatever source follows it. The
    async method declaration this comment documents must survive intact,
    not get swallowed into the (now-terminated) comment body.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "description": "Ends with a terminator */ right here.",
                }
            }
        },
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "Ends with a terminator * / right here." in source
    assert (
        "async train_model(payload: TrainModelRequest): Promise<TrainModelResponse> {"
        in source
    )
    assert source.count("{") == source.count("}")


def test_generate_typescript_sdk_and_wait_jsdoc_includes_the_operations_description(
    tmp_path,
):

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "description": "Train the classifier and return its accuracy.",
                    "x-notebook-to-api-async": True,
                }
            }
        },
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert source.count(" * Train the classifier and return its accuracy.") == 2
    assert "async train_model_and_wait(" in source


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

    assert (
        "async tasks_cleanup(payload: TasksCleanupRequest): "
        "Promise<TasksCleanupResponse> {"
    ) in source


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

    assert source.count(
        "async tasks_cleanup(payload: TasksCleanupRequest): "
        "Promise<TasksCleanupResponse> {"
    ) == 1
    assert source.count(
        "async tasks_cleanup_2(payload: TasksCleanup2Request): "
        "Promise<TasksCleanup2Response> {"
    ) == 1


@pytest.mark.parametrize(
    "colliding_name",
    [
        "getTask",
        "waitForTask",
        "listTasks",
        "deleteTask",
        "deleteCompletedTasks",
        "deleteFailedTasks",
        "request",
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

    pascal = _pascal_case(f"{colliding_name}_2")

    assert source.count(f"async {colliding_name}(") == 1
    assert (
        f"async {colliding_name}_2(payload: {pascal}Request): "
        f"Promise<{pascal}Response> {{"
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


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_wait_for_task_retries_transient_errors_before_succeeding(
    tmp_path,
):
    """Mirrors
    test_generate_python_sdk_wait_for_task_retries_transient_errors_before_succeeding
    for the TypeScript client: before this, waitForTask propagated *any*
    getTask() failure straight out of the polling loop, aborting the
    whole wait on a single transient 429/502/503/504 or network blip
    instead of retrying it like an ordinary still-processing task.
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
        let callCount = 0;
        globalThis.fetch = async (url, opts) => {{
          callCount += 1;
          if (callCount === 1) {{
            return {{ ok: false, status: 503 }};
          }}
          if (callCount === 2) {{
            throw new TypeError("fetch failed");
          }}
          return {{ ok: true, json: async () => ({{ status: "completed", result: 42 }}) }};
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
def test_generate_typescript_sdk_wait_for_task_still_throws_immediately_on_a_404(
    tmp_path,
):
    """A non-transient status (404, same as
    test_generate_python_sdk_wait_for_task_raises_for_a_not_found_task's
    Python-side equivalent) must still propagate on the first attempt,
    not be retried.
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
        let callCount = 0;
        globalThis.fetch = async (url, opts) => {{
          callCount += 1;
          return {{ ok: false, status: 404 }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        try {{
          await client.waitForTask("abc123", {{ pollIntervalMs: 0, timeoutMs: 5000 }});
          console.log(JSON.stringify({{ threw: false, callCount }}));
        }} catch (err) {{
          console.log(JSON.stringify({{ threw: true, message: err.message, callCount }}));
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
    assert "status 404" in output["message"]
    assert output["callCount"] == 1


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_every_method_attaches_status_to_a_thrown_error(
    tmp_path,
):
    """Confirmed exploitable before this fix: only getTask() (added so
    waitForTask could tell a transient failure from a real one) attached
    `.status` to the Error it throws for a non-ok response -- every other
    method (the shared `request()` helper every per-function endpoint and
    its own *_and_wait companion route through, listTasks, deleteTask,
    deleteCompletedTasks, deleteFailedTasks, and each of the hardcoded
    infra methods) still threw a bare Error with only a string message,
    so a caller of any of *those* had no way to programmatically
    distinguish e.g. a 401 from a 429 without regex-parsing the message.
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
        globalThis.fetch = async (url, opts) => ({{ ok: false, status: 429 }});

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000", {{ maxRetries: 0 }});

        const calls = [
          ["train_model", () => client.train_model({{}})],
          ["listTasks", () => client.listTasks()],
          ["deleteTask", () => client.deleteTask("x")],
          ["deleteCompletedTasks", () => client.deleteCompletedTasks()],
          ["deleteFailedTasks", () => client.deleteFailedTasks()],
          ["health", () => client.health()],
        ];

        const results = {{}};
        for (const [name, fn] of calls) {{
          try {{
            await fn();
            results[name] = "did not throw";
          }} catch (err) {{
            results[name] = err.status;
          }}
        }}

        console.log(JSON.stringify(results));
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

    results = json.loads(proc.stdout.strip().splitlines()[-1])
    assert results == {
        "train_model": 429,
        "listTasks": 429,
        "deleteTask": 429,
        "deleteCompletedTasks": 429,
        "deleteFailedTasks": 429,
        "health": 429,
    }


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
        "async train_model_and_wait(payload: TrainModelRequest, "
        "options: { pollIntervalMs?: number; timeoutMs?: number } = {}): "
        "Promise<TrainModelTaskResult> {" in source
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

    assert (
        "async listTasks(options: { status?: string; limit?: number; "
        "offset?: number } = {}): Promise<any> {"
    ) in source
    assert "async deleteTask(taskId: string): Promise<any> {" in source
    assert "async deleteCompletedTasks(): Promise<any> {" in source
    assert "async deleteFailedTasks(): Promise<any> {" in source


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_list_tasks_forwards_status_limit_offset(tmp_path):
    """Mirrors
    test_generate_python_sdk_list_tasks_forwards_status_limit_offset for
    the TypeScript client: before this, listTasks() accepted no
    arguments and always fetched a bare `/tasks`, with no way to reach
    the generated server's own status/limit/offset filtering short of
    bypassing the client entirely.
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
        const calls = [];
        globalThis.fetch = async (url, opts) => {{
          calls.push(url);
          return {{ ok: true, json: async () => ({{}}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        await client.listTasks();
        await client.listTasks({{ status: "failed" }});
        await client.listTasks({{ limit: 10, offset: 20 }});
        await client.listTasks({{ status: "completed", limit: 5, offset: 0 }});

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
        "http://localhost:8000/tasks",
        "http://localhost:8000/tasks?status=failed",
        "http://localhost:8000/tasks?limit=10&offset=20",
        "http://localhost:8000/tasks?status=completed&limit=5&offset=0",
    ]
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


def test_generate_typescript_sdk_includes_infrastructure_helpers(tmp_path):
    """Mirrors test_generate_python_sdk_includes_infrastructure_helpers for
    the TypeScript client.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "async health(): Promise<any> {" in source
    assert "async ready(): Promise<any> {" in source
    assert "async info(): Promise<any> {" in source
    assert "async config(): Promise<any> {" in source
    assert "async metrics(): Promise<any> {" in source
    assert "async uptime(): Promise<any> {" in source
    assert "async authStatus(): Promise<any> {" in source
    assert "async authInfo(): Promise<any> {" in source
    assert "async authValidate(): Promise<any> {" in source


def test_generate_typescript_sdk_infrastructure_helpers_send_correct_requests(tmp_path):

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
          return {{ ok: true, json: async () => ({{ status: "ok" }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        const results = [];
        results.push(await client.health());
        results.push(await client.ready());
        results.push(await client.info());
        results.push(await client.config());
        results.push(await client.metrics());
        results.push(await client.uptime());
        results.push(await client.authStatus());
        results.push(await client.authInfo());
        results.push(await client.authValidate());

        console.log(JSON.stringify({{ calls, results }}));
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
        {"url": "http://localhost:8000/health", "method": "GET"},
        {"url": "http://localhost:8000/ready", "method": "GET"},
        {"url": "http://localhost:8000/info", "method": "GET"},
        {"url": "http://localhost:8000/config", "method": "GET"},
        {"url": "http://localhost:8000/metrics", "method": "GET"},
        {"url": "http://localhost:8000/uptime", "method": "GET"},
        {"url": "http://localhost:8000/auth/status", "method": "GET"},
        {"url": "http://localhost:8000/auth/info", "method": "GET"},
        {"url": "http://localhost:8000/auth/validate", "method": "GET"},
    ]
    assert output["results"] == [{"status": "ok"}] * 9


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_retries_a_transient_failure_before_succeeding(
    tmp_path,
):
    """Mirrors generate_python_sdk's identical retry addition: only
    waitForTask's own polling loop ever retried a transient failure --
    every other call this client makes (a per-function POST, listTasks/
    deleteTask/..., health/ready/info/...) threw immediately on a single
    429/502/503/504, even though the generated server side already
    reports exactly when a caller should retry.
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
        globalThis.setTimeout = (fn) => fn();

        let calls = 0;
        globalThis.fetch = async (url, opts) => {{
          calls++;
          if (calls < 3) {{
            return {{ ok: false, status: 503, headers: {{ get: () => null }} }};
          }}
          return {{ ok: true, json: async () => ({{ result: 42 }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        const result = await client.train_model({{ a: 1 }});

        console.log(JSON.stringify({{ result, calls }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output["result"] == {"result": 42}
    assert output["calls"] == 3


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_does_not_retry_a_non_transient_failure(tmp_path):
    """A genuine 404/401/... must still throw on the very first attempt."""

    schema_path = _write_schema(
        tmp_path,
        {"/train_model": {"post": {"operationId": "train_model"}}},
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        globalThis.setTimeout = (fn) => {{ throw new Error("should not retry"); }};

        let calls = 0;
        globalThis.fetch = async (url, opts) => {{
          calls++;
          return {{ ok: false, status: 404, headers: {{ get: () => null }} }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        let status = null;
        try {{
          await client.train_model({{}});
        }} catch (err) {{
          status = err.status;
        }}

        console.log(JSON.stringify({{ status, calls }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output == {"status": 404, "calls": 1}


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_gives_up_after_max_retries(tmp_path):
    """A transient failure that never clears up must still eventually
    surface to the caller, bounded by maxRetries.
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
        globalThis.setTimeout = (fn) => fn();

        let calls = 0;
        globalThis.fetch = async (url, opts) => {{
          calls++;
          return {{ ok: false, status: 503, headers: {{ get: () => null }} }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000", {{ maxRetries: 2 }});

        let status = null;
        try {{
          await client.train_model({{}});
        }} catch (err) {{
          status = err.status;
        }}

        console.log(JSON.stringify({{ status, calls }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output == {"status": 503, "calls": 3}, "1 initial attempt + 2 retries"


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_retry_honors_retry_after_header(tmp_path):
    """A 429's own Retry-After header must drive the actual wait, not
    just the generic exponential backoff.
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
        const delays = [];
        globalThis.setTimeout = (fn, ms) => {{ delays.push(ms); fn(); }};

        let calls = 0;
        globalThis.fetch = async (url, opts) => {{
          calls++;
          if (calls === 1) {{
            return {{
              ok: false,
              status: 429,
              headers: {{ get: (name) => (name === "Retry-After" ? "7" : null) }},
            }};
          }}
          return {{ ok: true, json: async () => ({{ result: 1 }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        const result = await client.train_model({{}});

        console.log(JSON.stringify({{ result, delays }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output["result"] == {"result": 1}
    assert output["delays"] == [7000]


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_list_tasks_and_infra_methods_also_retry(tmp_path):
    """The retry/backoff addition isn't limited to the shared POST
    `request()` helper -- listTasks/deleteTask/... and the hardcoded
    health/ready/info/... methods route through the identical helper.
    """

    schema_path = _write_schema(tmp_path, {})
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        globalThis.setTimeout = (fn) => fn();

        let calls = 0;
        globalThis.fetch = async (url, opts) => {{
          calls++;
          if (calls === 1) {{
            return {{ ok: false, status: 503, headers: {{ get: () => null }} }};
          }}
          return {{ ok: true, json: async () => ({{ status: "ok" }}) }};
        }};

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        const result = await client.health();

        console.log(JSON.stringify({{ result, calls }}));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert output == {"result": {"status": "ok"}, "calls": 2}


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

def fake_post(url, json=None, headers=None, timeout=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.post("/" + path, json=json, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

def fake_get(url, headers=None, timeout=None):
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
result = client.add({{"a": 2, "b": 3}})
assert result == {{"result": 5}}, result

# GET /config (added in Commit #12) reached through the generated
# client's own config() method -- confirms this method isn't just
# generated source text, but actually hits the real compiled app's own
# new endpoint and gets its real runtime limits back.
config = client.config()
assert "max_request_body_bytes" in config, config
assert "rate_limit_per_minute" in config, config

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

def fake_post(url, json=None, headers=None, timeout=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.post("/" + path, json=json, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

def fake_get(url, headers=None, timeout=None):
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

def fake_post(url, json=None, headers=None, timeout=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.post("/" + path, json=json, headers=headers)
    resp.raise_for_status = lambda: None
    return resp

def fake_get(url, headers=None, timeout=None, params=None):
    path = url.split("://", 1)[1].split("/", 1)[1]
    resp = test_client.get("/" + path, headers=headers, params=params)
    resp.raise_for_status = lambda: None
    return resp

def fake_delete(url, headers=None, timeout=None):
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

# list_tasks' own status/limit/offset reach the real server: filtering
# to "completed" finds the just-finished task and nothing else.
filtered = client.list_tasks(status="completed")
assert second_id in filtered["tasks"], filtered
assert filtered["matching_tasks"] == 1, filtered

empty_page = client.list_tasks(status="completed", limit=1, offset=1)
assert empty_page["tasks"] == {{}}, empty_page

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


def test_python_type_to_typescript_maps_common_scalars():

    assert _python_type_to_typescript("int") == "number"
    assert _python_type_to_typescript("float") == "number"
    assert _python_type_to_typescript("str") == "string"
    assert _python_type_to_typescript("bool") == "boolean"
    assert _python_type_to_typescript("dict") == "Record<string, unknown>"


def test_python_type_to_typescript_falls_back_to_unknown():
    """A notebook-defined class/Enum name, or any typing construct this
    bounded mapper doesn't recognize, must fall back to "unknown" -- a
    safe, always-valid TypeScript type -- rather than emitting a bare
    identifier TypeScript has no declaration for at all.
    """

    assert _python_type_to_typescript("Priority") == "unknown"
    assert _python_type_to_typescript(None) == "unknown"
    assert _python_type_to_typescript("") == "unknown"


def test_python_type_to_typescript_maps_list_and_nested_generics():

    assert _python_type_to_typescript("List[int]") == "number[]"
    assert _python_type_to_typescript("list[str]") == "string[]"
    assert _python_type_to_typescript("List[List[int]]") == "number[][]"
    assert _python_type_to_typescript("Dict[str, int]") == "Record<string, number>"
    assert (
        _python_type_to_typescript("List[Dict[str, int]]")
        == "Record<string, number>[]"
    )


def test_python_type_to_typescript_maps_optional_and_union():

    assert _python_type_to_typescript("Optional[int]") == "number | null"
    assert _python_type_to_typescript("Optional[str]") == "string | null"
    assert _python_type_to_typescript("Union[int, str]") == "number | string"
    assert _python_type_to_typescript("int | None") == "number | null"
    assert _python_type_to_typescript("int | str") == "number | string"


def test_python_type_to_typescript_parenthesizes_a_union_array_element():
    """"number | null[]" parses in TypeScript as "number | (null[])", not
    the intended "(number | null)[]" -- a union used as an array element
    type must be parenthesized.
    """

    assert _python_type_to_typescript("List[Optional[int]]") == "(number | null)[]"


def test_python_type_to_typescript_unwraps_annotated():
    """Annotated[T, ...] carries extra (non-type) metadata as its own
    trailing arguments -- only the first one, T, is the actual type.
    """

    assert (
        _python_type_to_typescript('Annotated[int, Field(gt=0)]') == "number"
    )


def test_json_schema_type_to_typescript_maps_common_types():

    assert _json_schema_type_to_typescript({"type": "integer"}) == "number"
    assert _json_schema_type_to_typescript({"type": "number"}) == "number"
    assert _json_schema_type_to_typescript({"type": "string"}) == "string"
    assert _json_schema_type_to_typescript({"type": "boolean"}) == "boolean"
    assert _json_schema_type_to_typescript({"type": "object"}) == "Record<string, unknown>"
    assert _json_schema_type_to_typescript({}) == "unknown"
    assert _json_schema_type_to_typescript(None) == "unknown"


def test_json_schema_type_to_typescript_maps_arrays():

    assert (
        _json_schema_type_to_typescript(
            {"type": "array", "items": {"type": "string"}}
        )
        == "string[]"
    )
    assert (
        _json_schema_type_to_typescript({"type": "array", "items": {}}) == "unknown[]"
    )


def test_json_schema_type_to_typescript_maps_nullable_anyof():
    """Pydantic v2's own JSON schema represents Optional[str] as an
    "anyOf" of {"type": "string"} and {"type": "null"} -- not a "type"
    field directly.
    """

    assert (
        _json_schema_type_to_typescript(
            {"anyOf": [{"type": "string"}, {"type": "null"}]}
        )
        == "string | null"
    )


def test_pascal_case_converts_snake_case_method_names():

    assert _pascal_case("train_model") == "TrainModel"
    assert _pascal_case("tasks_cleanup_2") == "TasksCleanup2"
    assert _pascal_case("add") == "Add"


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_types_request_fields_from_the_real_json_schema(
    tmp_path,
):
    """Confirmed missing before this feature: every generated method's
    own payload parameter and return value were typed as a bare
    Record<string, unknown>/any regardless of what the notebook function
    actually expects or returns -- throwing away the single biggest
    practical reason to generate a *TypeScript* client over a plain JS
    one (compile-time type checking, IDE autocomplete).
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-return-type": "dict",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/TrainModelRequest"
                                }
                            }
                        }
                    },
                }
            }
        },
    )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["components"] = {
        "schemas": {
            "TrainModelRequest": {
                "properties": {
                    "epochs": {"type": "integer"},
                    "lr": {"type": "number", "default": 0.01},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "note": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                },
                "required": ["epochs", "tags"],
            }
        }
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    client_path = tmp_path / "client.ts"
    generate_typescript_sdk(str(schema_path), str(client_path))

    source = client_path.read_text(encoding="utf-8")

    assert "export interface TrainModelRequest {" in source
    assert "epochs: number;" in source
    assert "lr?: number;" in source
    assert "tags: string[];" in source
    assert "note?: string | null;" in source
    assert "export interface TrainModelResponse {" in source
    assert "result: Record<string, unknown>;" in source
    assert (
        "async train_model(payload: TrainModelRequest): "
        "Promise<TrainModelResponse> {" in source
    )

    # Braces must balance -- confirms the interface block appended after
    # the class didn't break overall structure.
    assert source.count("{") == source.count("}")

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        globalThis.fetch = async (url, opts) => ({{
          ok: true,
          json: async () => ({{ result: {{ accuracy: 0.9 }} }}),
        }});

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        const result = await client.train_model({{
          epochs: 5,
          tags: ["a", "b"],
        }});

        console.log(JSON.stringify(result));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {
        "result": {"accuracy": 0.9}
    }


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="requires a Node.js runtime to execute the generated TypeScript client",
)
def test_generate_typescript_sdk_background_endpoint_gets_typed_submission_and_result_interfaces(
    tmp_path,
):
    """A background endpoint's own two distinct response shapes -- the
    immediate {task_id, status: "processing"} submission, and the
    eventually-finished task record its own *_and_wait companion
    resolves to -- must each get their own correctly-typed interface,
    not share one generic Promise<any> the way both used to.
    """

    schema_path = _write_schema(
        tmp_path,
        {
            "/train_model": {
                "post": {
                    "operationId": "train_model",
                    "x-notebook-to-api-async": True,
                    "x-notebook-to-api-return-type": "float",
                }
            }
        },
    )
    client_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(client_path))

    source = client_path.read_text(encoding="utf-8")

    assert "export interface TrainModelResponse {" in source
    assert '  status: "processing";' in source
    assert "export interface TrainModelTaskResult {" in source
    assert "result?: number;" in source
    assert (
        "async train_model(payload: TrainModelRequest): "
        "Promise<TrainModelResponse> {" in source
    )
    assert (
        "async train_model_and_wait(payload: TrainModelRequest, "
        "options: { pollIntervalMs?: number; timeoutMs?: number } = {}): "
        "Promise<TrainModelTaskResult> {" in source
    )

    runner_path = tmp_path / "run.mjs"
    runner_path.write_text(
        f"""
        globalThis.fetch = async (url, opts) => ({{
          ok: true,
          json: async () => ({{ task_id: "abc123", status: "processing" }}),
        }});

        const {{ NotebookAPIClient }} = await import({json.dumps(str(client_path))});
        const client = new NotebookAPIClient("http://localhost:8000");

        const submitted = await client.train_model({{}});
        console.log(JSON.stringify(submitted));
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(runner_path)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {
        "task_id": "abc123",
        "status": "processing",
    }


def test_generate_typescript_sdk_request_interface_falls_back_for_no_request_body(
    tmp_path,
):
    """A hardcoded built-in POST route (e.g. /tasks/cleanup, /tasks/reset)
    has no request body schema at all -- its own {Pascal}Request
    interface must still be valid TypeScript (an index signature),
    not an empty, pointless `{}` body.
    """

    schema_path = _write_schema(
        tmp_path,
        {"/tasks/cleanup": {"post": {"operationId": "cleanup_tasks"}}},
    )
    output_path = tmp_path / "client.ts"

    generate_typescript_sdk(str(schema_path), str(output_path))

    source = output_path.read_text(encoding="utf-8")

    assert "export interface TasksCleanupRequest {" in source
    assert "[key: string]: unknown;" in source
