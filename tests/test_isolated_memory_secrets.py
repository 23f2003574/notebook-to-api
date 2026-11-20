"""Isolated notebook code cannot read service credentials from the app's memory."""
import json
import os
import socket
import subprocess
import sys
import time
import types
import urllib.request
from pathlib import Path

import nbformat
import pytest

from backend.runtime.isolated_execution import run_isolated

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="isolation needs fork")


def _install_fake_app(monkeypatch):
    module = types.ModuleType("fake_service_app")
    module.API_KEYS = ("alpha-secret", "beta-secret")
    module.WEBHOOK_SECRET = "hook-secret"
    module.CONFIG = {"title": "alpha-secret"}  # not a credential-named attribute: left alone
    module.EXTRA_TOKENS = ["alpha-secret", "other"]
    monkeypatch.setitem(sys.modules, "fake_service_app", module)
    return module


def _peek():
    module = sys.modules["fake_service_app"]
    return [module.API_KEYS, module.WEBHOOK_SECRET, module.CONFIG["title"], module.EXTRA_TOKENS]


def test_child_sees_blank_credentials_and_the_service_keeps_them(monkeypatch):
    module = _install_fake_app(monkeypatch)
    monkeypatch.setenv("NOTEBOOK_API_KEY", "alpha-secret,beta-secret")
    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_SECRET", "hook-secret")
    keys, hook, title, extra = run_isolated(_peek, (), timeout=20)
    assert tuple(keys) == ()
    assert hook == "" and extra == []
    assert title == "alpha-secret"  # only credential-named attributes are touched
    assert module.API_KEYS == ("alpha-secret", "beta-secret") and module.WEBHOOK_SECRET == "hook-secret"
    assert module.EXTRA_TOKENS == ["alpha-secret", "other"]


def test_allowlisted_credentials_stay_visible(monkeypatch):
    _install_fake_app(monkeypatch)
    monkeypatch.setenv("NOTEBOOK_API_KEY", "alpha-secret,beta-secret")
    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_ALLOW_ENV", "NOTEBOOK_API_WEBHOOK_SECRET")
    keys, hook, _, _ = run_isolated(_peek, (), timeout=20)
    assert tuple(keys) == () and hook == "hook-secret"


def test_unrelated_values_equal_to_nothing_are_not_blanked(monkeypatch):
    module = _install_fake_app(monkeypatch)
    module.SOME_KEY = "unrelated"
    monkeypatch.setenv("NOTEBOOK_API_KEY", "alpha-secret")
    assert run_isolated(lambda: sys.modules["fake_service_app"].SOME_KEY, (), timeout=20) == "unrelated"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


NOTEBOOK = '''
import os, sys
def peek():
    secret = os.environ.get("SERVICE_SECRET_PROBE", "")
    leaked = False
    for name, module in list(sys.modules.items()):
        for attribute, value in list(getattr(module, "__dict__", {}).items()):
            if "TOPSECRET" in str(value) and not attribute.startswith("__"):
                leaked = leaked or attribute in ("API_KEYS", "DEFAULT_API_KEY") or "KEY" in attribute
    return {"env": os.environ.get("NOTEBOOK_API_KEY"), "leaked_through_globals": leaked,
            "proc_self": b"TOPSECRET" in open("/proc/self/environ", "rb").read() if os.path.exists("/proc/self/environ") else False}
def train_peek():
    return peek()
'''


@pytest.fixture
def server(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(NOTEBOOK))
    nbformat.write(nb, tmp_path / "nb.ipynb")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-m", "backend.cli", "compile", str(tmp_path / "nb.ipynb"), "--output", str(tmp_path / "memapp")],
                          capture_output=True, text=True, env=env, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "memapp.app:app", "--port", str(port), "--log-level", "warning"],
        cwd=tmp_path, env={**env, "PYTHONPATH": f"{tmp_path}:{ROOT}", "NOTEBOOK_API_KEY": "TOPSECRET", "NOTEBOOK_API_ISOLATE_EXECUTION": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    def call(path, method="GET", body=None, key="TOPSECRET"):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-API-Key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            call("/health")
            break
        except OSError:
            time.sleep(0.3)
    else:
        process.kill()
        raise AssertionError(process.stdout.read().decode())
    try:
        yield call
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_real_server_synchronous_call_cannot_read_the_key(server):
    result = server("/peek", "POST", {})["result"]
    assert result["env"] is None and result["leaked_through_globals"] is False
    if os.path.exists("/proc/self/environ"):
        assert result["proc_self"] is False


def test_real_server_background_task_cannot_read_the_key_and_the_service_still_works(server):
    task_id = server("/train_peek", "POST", {})["task_id"]
    for _ in range(100):
        task = server(f"/tasks/{task_id}")
        if task["status"] != "processing":
            break
        time.sleep(0.2)
    assert task["status"] == "completed" and task["result"]["leaked_through_globals"] is False and task["result"]["env"] is None
    assert server("/health")  # the parent still has its credentials and serves
    assert server("/peek", "POST", {})["result"]["leaked_through_globals"] is False  # repeatedly, not once
