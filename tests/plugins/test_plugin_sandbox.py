import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_loader import PluginLoader, PluginManifest
from backend.plugins.plugin_sandbox import (
    Sandbox,
    SandboxAlreadyExistsError,
    SandboxFilesystemViolationError,
    SandboxPolicy,
    SandboxResourceLimitExceededError,
    SandboxTimeoutError,
    PluginSandbox,
    UnknownSandboxError,
    get_plugin_loader_dependency,
    get_plugin_sandbox,
    router as plugin_sandbox_router,
)


@pytest.fixture
def sandbox() -> PluginSandbox:
    return PluginSandbox()


def test_create_returns_sandbox(sandbox: PluginSandbox):
    created = sandbox.create("csv-exporter", SandboxPolicy(timeout_seconds=2.0))

    assert isinstance(created, Sandbox)
    assert created.plugin == "csv-exporter"
    assert created.policy.timeout_seconds == 2.0


def test_create_uses_default_policy(sandbox: PluginSandbox):
    created = sandbox.create("csv-exporter")

    assert created.policy.timeout_seconds == 5.0


def test_create_duplicate_raises(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")

    with pytest.raises(SandboxAlreadyExistsError):
        sandbox.create("csv-exporter")


def test_create_after_destroy_is_allowed(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")
    sandbox.destroy("csv-exporter")

    created = sandbox.create("csv-exporter")

    assert created.plugin == "csv-exporter"


def test_destroy_unknown_raises(sandbox: PluginSandbox):
    with pytest.raises(UnknownSandboxError):
        sandbox.destroy("does-not-exist")


def test_destroy_twice_raises(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")
    sandbox.destroy("csv-exporter")

    with pytest.raises(UnknownSandboxError):
        sandbox.destroy("csv-exporter")


def test_status_unknown_raises(sandbox: PluginSandbox):
    with pytest.raises(UnknownSandboxError):
        sandbox.status("does-not-exist")


def test_status_reports_active_then_destroyed(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")
    assert sandbox.status("csv-exporter")["status"] == "active"

    sandbox.destroy("csv-exporter")
    assert sandbox.status("csv-exporter")["status"] == "destroyed"


def test_execute_runs_function_and_returns_result(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")

    result = sandbox.execute("csv-exporter", lambda a, b: a + b, 2, 3)

    assert result == 5


def test_execute_unknown_sandbox_raises(sandbox: PluginSandbox):
    with pytest.raises(UnknownSandboxError):
        sandbox.execute("does-not-exist", lambda: None)


def test_execute_after_destroy_raises(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")
    sandbox.destroy("csv-exporter")

    with pytest.raises(UnknownSandboxError):
        sandbox.execute("csv-exporter", lambda: None)


def test_execute_increments_execution_count(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")

    sandbox.execute("csv-exporter", lambda: None)
    sandbox.execute("csv-exporter", lambda: None)

    assert sandbox.status("csv-exporter")["execution_count"] == 2


def test_execute_propagates_function_exceptions(sandbox: PluginSandbox):
    sandbox.create("csv-exporter")

    def boom():
        raise ValueError("plugin blew up")

    with pytest.raises(ValueError, match="plugin blew up"):
        sandbox.execute("csv-exporter", boom)


def test_execute_enforces_timeout(sandbox: PluginSandbox):
    sandbox.create("csv-exporter", SandboxPolicy(timeout_seconds=0.05))

    with pytest.raises(SandboxTimeoutError):
        sandbox.execute("csv-exporter", time.sleep, 1.0)


def test_execute_allows_fast_function_within_timeout(sandbox: PluginSandbox):
    sandbox.create("csv-exporter", SandboxPolicy(timeout_seconds=1.0))

    result = sandbox.execute("csv-exporter", lambda: "done")

    assert result == "done"


def test_execute_enforces_cpu_limit(sandbox: PluginSandbox):
    sandbox.create("csv-exporter", SandboxPolicy(timeout_seconds=5.0, max_cpu_seconds=0.0001))

    def burn_cpu():
        total = 0
        for i in range(5_000_000):
            total += i
        return total

    with pytest.raises(SandboxResourceLimitExceededError):
        sandbox.execute("csv-exporter", burn_cpu)


def test_execute_allows_when_cpu_within_limit(sandbox: PluginSandbox):
    sandbox.create("csv-exporter", SandboxPolicy(timeout_seconds=5.0, max_cpu_seconds=2.0))

    result = sandbox.execute("csv-exporter", lambda: 1 + 1)

    assert result == 2


def test_execute_blocks_disallowed_path(sandbox: PluginSandbox, tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    forbidden_file = tmp_path / "forbidden.txt"
    forbidden_file.write_text("secret")
    sandbox.create("csv-exporter", SandboxPolicy(allowed_paths=(str(allowed_dir),)))

    def read_forbidden():
        with open(forbidden_file) as handle:
            return handle.read()

    with pytest.raises(SandboxFilesystemViolationError):
        sandbox.execute("csv-exporter", read_forbidden)


def test_execute_allows_permitted_path(sandbox: PluginSandbox, tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    allowed_file = allowed_dir / "data.txt"
    allowed_file.write_text("hello")
    sandbox.create("csv-exporter", SandboxPolicy(allowed_paths=(str(allowed_dir),)))

    def read_allowed():
        with open(allowed_file) as handle:
            return handle.read()

    result = sandbox.execute("csv-exporter", read_allowed)

    assert result == "hello"


def test_execute_without_allowed_paths_does_not_restrict(sandbox: PluginSandbox, tmp_path):
    unrestricted_file = tmp_path / "anything.txt"
    unrestricted_file.write_text("unrestricted")
    sandbox.create("csv-exporter")

    def read_file():
        with open(unrestricted_file) as handle:
            return handle.read()

    assert sandbox.execute("csv-exporter", read_file) == "unrestricted"


# --- API tests -------------------------------------------------------------


def _unique_module_name() -> str:
    return f"sandbox_plugin_{uuid.uuid4().hex}"


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
    import sys

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def _write(body: str) -> str:
        module_name = _unique_module_name()
        (tmp_path / f"{module_name}.py").write_text(body)
        return module_name

    return _write


@pytest.fixture
def loader() -> PluginLoader:
    return PluginLoader()


@pytest.fixture
def client(sandbox: PluginSandbox, loader: PluginLoader) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_sandbox_router)
    app.dependency_overrides[get_plugin_sandbox] = lambda: sandbox
    app.dependency_overrides[get_plugin_loader_dependency] = lambda: loader
    return TestClient(app)


def test_api_create_then_get_status(client: TestClient):
    response = client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})
    assert response.status_code == 201

    status_response = client.get("/plugins/sandbox/csv-exporter")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "active"


def test_api_create_duplicate_returns_409(client: TestClient):
    client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})

    response = client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})

    assert response.status_code == 409


def test_api_get_status_unknown_returns_404(client: TestClient):
    response = client.get("/plugins/sandbox/does-not-exist")

    assert response.status_code == 404


def test_api_delete_sandbox(client: TestClient):
    client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})

    response = client.delete("/plugins/sandbox/csv-exporter")
    assert response.status_code == 204

    assert client.get("/plugins/sandbox/csv-exporter").json()["status"] == "destroyed"


def test_api_execute_requires_loaded_plugin(client: TestClient):
    client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})

    response = client.post("/plugins/sandbox/csv-exporter/execute", json={})

    assert response.status_code == 404


def test_api_execute_requires_main_entry_point(client: TestClient, loader: PluginLoader, plugin_module):
    module_name = plugin_module("VALUE = 1\n")
    loader.load(PluginManifest(name="csv-exporter", version="1.0.0", entry_point=module_name))
    client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})

    response = client.post("/plugins/sandbox/csv-exporter/execute", json={})

    assert response.status_code == 422


def test_api_execute_calls_main_and_returns_result(client: TestClient, loader: PluginLoader, plugin_module):
    module_name = plugin_module("def main(x):\n    return x * 2\n")
    loader.load(PluginManifest(name="csv-exporter", version="1.0.0", entry_point=module_name))
    client.post("/plugins/sandbox", json={"plugin": "csv-exporter"})

    response = client.post("/plugins/sandbox/csv-exporter/execute", json={"kwargs": {"x": 21}})

    assert response.status_code == 200
    assert response.json() == {"plugin": "csv-exporter", "result": 42}


def test_api_execute_timeout_returns_504(client: TestClient, loader: PluginLoader, plugin_module):
    module_name = plugin_module("import time\n\ndef main():\n    time.sleep(1.0)\n")
    loader.load(PluginManifest(name="csv-exporter", version="1.0.0", entry_point=module_name))
    client.post(
        "/plugins/sandbox",
        json={"plugin": "csv-exporter", "policy": {"timeout_seconds": 0.05}},
    )

    response = client.post("/plugins/sandbox/csv-exporter/execute", json={})

    assert response.status_code == 504
