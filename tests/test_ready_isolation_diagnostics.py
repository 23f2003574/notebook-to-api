"""/ready reports effective isolation and resource-limit state."""
import importlib
import json
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

KEYS = {"state", "platform_supported", "platform_problem", "request_timeout_seconds", "task_timeout_seconds",
        "memory_limit_mb", "cpu_limit_seconds", "active_executions", "max_concurrent_executions",
        "max_concurrent_calls"}


def _boot(tmp_path, monkeypatch, env):
    if not (tmp_path / "diag_app").exists():
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell("def ping() -> int:\n    return 1\n")]
        nbformat.write(nb, str(tmp_path / "nb.ipynb"))
        compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "diag_app"))
        monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "super-secret-diag-key")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for name in [k for k in sys.modules if k.startswith("diag_app")]:
        del sys.modules[name]
    return importlib.import_module("diag_app.app")


def test_disabled_by_default_with_stable_schema_and_no_secrets(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch, {})
    body = TestClient(module.app).get("/ready").json()
    iso = body["isolation"]
    assert body["status"] == "ready" and set(iso) == KEYS and iso["state"] == "disabled"
    assert iso["memory_limit_mb"] == {"configured": None, "enforced": False}
    assert iso["cpu_limit_seconds"] == {"configured": None, "enforced": False}
    assert iso["active_executions"] == 0 and iso["max_concurrent_executions"] is None
    text = json.dumps(body)
    assert "super-secret-diag-key" not in text and str(tmp_path) not in text


def test_enabled_with_limits_reports_effective_values(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch, {
        "NOTEBOOK_API_ISOLATE_EXECUTION": "1", "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB": "512",
        "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS": "5", "NOTEBOOK_API_ISOLATE_MAX_PROCESSES": "3",
        "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS": "7", "NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS": "9",
    })
    iso = TestClient(module.app).get("/ready").json()["isolation"]
    assert iso["state"] == "enabled" and iso["platform_supported"] is True and iso["platform_problem"] is None
    assert iso["request_timeout_seconds"] == 7 and iso["task_timeout_seconds"] == 9
    assert iso["max_concurrent_executions"] == 3
    assert iso["cpu_limit_seconds"] == {"configured": 5, "enforced": True}
    assert iso["memory_limit_mb"]["configured"] == 512
    assert iso["memory_limit_mb"]["enforced"] is (sys.platform != "darwin")


def test_active_execution_count_tracks_running_children(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch, {"NOTEBOOK_API_ISOLATE_EXECUTION": "1"})
    client = TestClient(module.app)
    with module._ACTIVE_LOCK:
        module._ACTIVE[999999] = object()
    try:
        assert client.get("/ready").json()["isolation"]["active_executions"] == 1
    finally:
        with module._ACTIVE_LOCK:
            module._ACTIVE.pop(999999)
    assert client.get("/ready").json()["isolation"]["active_executions"] == 0


@pytest.mark.parametrize("enabled", [False])
def test_unsupported_platform_is_reported_not_claimed_enforced(tmp_path, monkeypatch, enabled):
    module = _boot(tmp_path, monkeypatch, {})
    monkeypatch.setattr(module, "isolation_platform_problem", lambda *a, **k: "this platform lacks process groups (os.killpg)")
    iso = TestClient(module.app).get("/ready").json()["isolation"]
    assert iso["platform_supported"] is False and "killpg" in iso["platform_problem"]
    assert iso["memory_limit_mb"]["enforced"] is False and iso["cpu_limit_seconds"]["enforced"] is False
