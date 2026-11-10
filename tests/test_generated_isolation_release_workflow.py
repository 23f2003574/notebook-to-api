import os
import time

import pytest
from fastapi.testclient import TestClient

from backend.generator.api_generator import generate_fastapi_code
from tests.test_generator import _register_fake_notebook_module

HEADERS = {"X-API-Key": "notebook-to-api-dev-key"}
LIMIT_VARS = [
    "NOTEBOOK_API_ISOLATE_EXECUTION",
    "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB",
    "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS",
    "NOTEBOOK_API_ISOLATE_MAX_PROCESSES",
    "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS",
]


def _spin(pid_path):
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    while True:
        pass


def _app(monkeypatch):
    functions = [
        {"name": "whoami", "args": [], "return_type": "int"},
        {"name": "spin", "args": [{"name": "pid_path", "type": "str"}], "return_type": "int"},
    ]
    code = generate_fastapi_code(functions)
    _register_fake_notebook_module(monkeypatch)
    ns = {"__name__": "generated_app"}
    exec(compile(code, "<generated>", "exec"), ns)
    ns["notebook_module"].whoami = os.getpid
    ns["notebook_module"].spin = _spin
    return ns, TestClient(ns["app"])


def _call_with_capacity_retry(client, path, body):
    deadline = time.monotonic() + 10
    while True:
        r = client.post(path, json=body, headers=HEADERS)
        if r.status_code != 503 or time.monotonic() > deadline:
            return r
        time.sleep(0.05)


def test_defaults_are_backward_compatible_in_process_and_unlimited(monkeypatch):
    for name in LIMIT_VARS:
        monkeypatch.delenv(name, raising=False)
    ns, client = _app(monkeypatch)
    assert ns["ISOLATE_EXECUTION"] is False
    assert (ns["ISOLATE_MEMORY_LIMIT_MB"], ns["ISOLATE_CPU_LIMIT_SECONDS"], ns["ISOLATE_MAX_PROCESSES"]) == (0, 0, 0)
    assert ns["REQUEST_TIMEOUT_SECONDS"] == 0
    # No isolation unless asked for: the notebook function runs in this very process.
    assert client.post("/whoami", json={}, headers=HEADERS).json() == {"result": os.getpid()}


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX isolation")
def test_all_limits_configured_together_run_timeout_cleanup_and_recover(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_EXECUTION", "1")
    monkeypatch.setenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB", "4096")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS", "30")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_MAX_PROCESSES", "1")
    ns, client = _app(monkeypatch)

    # Within the configured limits: runs, but in a separate child process.
    ok = client.post("/whoami", json={}, headers=HEADERS)
    assert ok.status_code == 200 and ok.json()["result"] != os.getpid()

    # Non-terminating call: controlled 504 (wall-clock timeout; the memory/CPU
    # limits are configured but deliberately not what ends this call).
    pid_path = str(tmp_path / "child.pid")
    timed_out = client.post("/spin", json={"pid_path": pid_path}, headers=HEADERS)
    assert timed_out.status_code == 504
    assert "Traceback" not in timed_out.text

    # The child is really gone (killed and reaped), and its slot is free again.
    child = int(open(pid_path).read())
    deadline = time.monotonic() + 10
    while True:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "child survived the timeout"
        time.sleep(0.05)

    again = _call_with_capacity_retry(client, "/whoami", {})
    assert again.status_code == 200 and again.json()["result"] != os.getpid()
