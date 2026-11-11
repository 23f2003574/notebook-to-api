"""End to end: a compiled app with process isolation on serves real requests,
reports readiness, and on shutdown leaves no child or subprocess behind even
when a request is still running. Unit tests cover each piece; nothing drove the
compiled app through serve -> in-flight request -> shutdown together."""

import importlib
import os
import threading
import time

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="isolation needs POSIX process groups")
HEADERS = {"X-API-Key": "lifecycle-key"}


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _compile(tmp_path, monkeypatch, pidfile):
    source = (
        "import os, subprocess, time\n\n"
        "def double(x: int) -> int:\n"
        '    """Double x."""\n'
        "    return x * 2\n\n"
        "def hang() -> int:\n"
        '    """Start a subprocess, then block."""\n'
        '    child = subprocess.Popen(["sleep", "60"])\n'
        f'    open({str(pidfile)!r}, "w").write(f"{{os.getpid()}} {{child.pid}}")\n'
        "    time.sleep(60)\n"
        "    return 1\n"
    )
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(source)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "iso_app"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "lifecycle-key")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_EXECUTION", "1")
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module("iso_app.app")


def test_isolated_app_serves_reports_ready_and_cleans_up_in_flight_work_on_shutdown(tmp_path, monkeypatch):
    pidfile = tmp_path / "pids"
    module = _compile(tmp_path, monkeypatch, pidfile)
    outcome = {}
    pids = []
    client = TestClient(module.app)
    client.__enter__()  # runs the app's lifespan startup
    try:
        assert client.get("/health").json() == {"status": "healthy"}
        assert client.get("/ready").status_code == 200
        assert client.post("/double", json={"x": 21}, headers=HEADERS).json() == {"result": 42}  # served by an isolated child

        def call_hang():
            try:
                outcome["status"] = TestClient(module.app).post("/hang", json={}, headers=HEADERS).status_code
            except Exception as exc:  # noqa: BLE001 - connection torn down by shutdown
                outcome["status"] = type(exc).__name__

        caller = threading.Thread(target=call_hang, daemon=True)
        caller.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text().count(" ")):
            time.sleep(0.05)
        pids = list(map(int, pidfile.read_text().split()))
        assert all(_alive(p) for p in pids) and module.terminate_active_processes is not None
    finally:
        client.__exit__(None, None, None)  # lifespan shutdown: marks not-ready, stops isolated children
        for pid in pids:
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    assert module._LIFECYCLE["state"] == "shutting_down"
    assert not any(_alive(p) for p in pids)
    caller.join(20)
    assert not caller.is_alive() and outcome["status"] != 200
