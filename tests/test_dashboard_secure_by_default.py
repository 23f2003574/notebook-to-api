"""The dashboard never serves its administrative API unauthenticated on a network interface."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.dashboard import (
    app,
    dashboard_host,
    host_is_loopback,
    validate_dashboard_exposure,
)

ROOT = Path(__file__).resolve().parent.parent


def test_default_host_is_loopback(monkeypatch):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_HOST", raising=False)
    assert dashboard_host() == "127.0.0.1"


@pytest.mark.parametrize("host", ["127.0.0.1", "127.5.5.5", "::1", "[::1]", "localhost"])
def test_loopback_hosts_need_no_key(host):
    assert host_is_loopback(host)
    validate_dashboard_exposure(host, "")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.com", ""])
def test_non_loopback_without_key_is_refused(host):
    with pytest.raises(ValueError, match="NOTEBOOK_API_DASHBOARD_KEY"):
        validate_dashboard_exposure(host, "")


def test_non_loopback_with_key_is_allowed_and_key_is_not_echoed():
    validate_dashboard_exposure("0.0.0.0", "s3cret-value")
    with pytest.raises(ValueError) as info:
        validate_dashboard_exposure("0.0.0.0", "")
    assert "s3cret-value" not in str(info.value)


def _run_main(env):
    script = f"""
import sys, runpy, uvicorn
sys.path.insert(0, {str(ROOT)!r})
uvicorn.run = lambda *a, **k: print("STARTED", k["host"])
runpy.run_module("backend.dashboard", run_name="__main__")
"""
    base = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_DASHBOARD")}
    return subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), capture_output=True,
                          text=True, timeout=120, env={**base, **env})


def test_running_the_module_defaults_to_loopback_and_starts():
    proc = _run_main({})
    assert proc.returncode == 0, proc.stderr
    assert "STARTED 127.0.0.1" in proc.stdout


def test_running_the_module_on_all_interfaces_without_key_fails_before_uvicorn():
    proc = _run_main({"NOTEBOOK_API_DASHBOARD_HOST": "0.0.0.0"})
    assert proc.returncode != 0
    assert "STARTED" not in proc.stdout
    assert "NOTEBOOK_API_DASHBOARD_KEY" in proc.stderr


def test_running_the_module_on_all_interfaces_with_key_starts():
    proc = _run_main({"NOTEBOOK_API_DASHBOARD_HOST": "0.0.0.0", "NOTEBOOK_API_DASHBOARD_KEY": "zq-secret-7731"})
    assert proc.returncode == 0, proc.stderr
    assert "STARTED 0.0.0.0" in proc.stdout
    assert "zq-secret-7731" not in proc.stdout + proc.stderr


def test_launch_script_starts_the_module_and_stops_if_it_fails():
    text = (ROOT / "start-dashboard.sh").read_text(encoding="utf-8")
    assert "python -m backend.dashboard" in text
    assert "kill -0 $BACKEND_PID" in text and text.index("kill -0") < text.index("npm run dev")


def test_authenticated_routes_still_work_with_a_key(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "k")
    client = TestClient(app)
    assert client.get("/api/secrets/x").status_code == 401
    assert client.get("/api/api-keys", headers={"X-API-Key": "k"}).status_code != 401
