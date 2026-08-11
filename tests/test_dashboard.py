import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.dashboard import app, dashboard_host, dashboard_port

PROJECT_ROOT = Path(__file__).resolve().parents[1]

client = TestClient(app)


def test_dashboard_host_defaults_to_all_interfaces(monkeypatch):

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_HOST", raising=False)

    assert dashboard_host() == "0.0.0.0"


def test_dashboard_host_env_var_overrides_the_default(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_HOST", "127.0.0.1")

    assert dashboard_host() == "127.0.0.1"


def test_dashboard_port_defaults_to_8001(monkeypatch):
    """8001, not 8000 -- 8000 is the *generated* app's own default (see
    `serve --port` and the generated Dockerfile's EXPOSE 8000), and the
    bundled frontend already calls the dashboard API on 8001 (see
    frontend/src/components/Dashboard.jsx). Defaulting both services to
    the same port would make running them side by side impossible
    without one of them already overriding it.
    """

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_PORT", raising=False)

    assert dashboard_port() == 8001


def test_dashboard_port_env_var_overrides_the_default(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_PORT", "9231")

    assert dashboard_port() == 9231


def test_root_endpoint_reports_service_metadata():
    """GET / had no test coverage at all before this."""

    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["service"] == "notebook-to-api Dashboard API"
    assert body["docs"] == "/docs"


def test_dashboard_module_run_directly_passes_configured_host_and_port_to_uvicorn():
    """The `if __name__ == "__main__":` block (what `python -m
    backend.dashboard` actually executes) had zero test coverage before
    this -- exercised here by running the module as __main__ in a
    subprocess, with uvicorn.run replaced by a stub so nothing actually
    tries to bind a port and block. Run in a subprocess (mirroring
    test_dashboard_cors.py's env-var override test) since dashboard_host/
    dashboard_port must be re-read from the environment at process start,
    not from whatever's already been imported in this test process.
    """

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

import uvicorn

captured = {{}}

def fake_run(app_path, **kwargs):
    captured["app_path"] = app_path
    captured.update(kwargs)

uvicorn.run = fake_run

import runpy
runpy.run_module("backend.dashboard", run_name="__main__")

assert captured["app_path"] == "backend.dashboard:app", captured
assert captured["host"] == "127.0.0.1", captured
assert captured["port"] == 9231, captured
assert captured["reload"] is True, captured

print("DASHBOARD_MAIN_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_DASHBOARD_HOST": "127.0.0.1",
            "NOTEBOOK_API_DASHBOARD_PORT": "9231",
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DASHBOARD_MAIN_OK" in proc.stdout


def test_dashboard_module_run_directly_defaults_to_8001_without_env_override():

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

import uvicorn

captured = {{}}

def fake_run(app_path, **kwargs):
    captured.update(kwargs)

uvicorn.run = fake_run

import runpy
runpy.run_module("backend.dashboard", run_name="__main__")

assert captured["host"] == "0.0.0.0", captured
assert captured["port"] == 8001, captured

print("DASHBOARD_MAIN_DEFAULT_OK")
"""

    env = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_DASHBOARD_")}

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DASHBOARD_MAIN_DEFAULT_OK" in proc.stdout
