"""POST /tasks/{id}/cancel terminates the task's real isolated child."""
import asyncio
import importlib
import os
import threading
import time

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="isolation needs POSIX process groups")
HEADERS = {"X-API-Key": "cancel-key"}
SOURCE = (
    "import os, subprocess, time\n\n"
    "# notebook-to-api: background\n"
    "def hang_job(pidfile: str) -> int:\n"
    '    """Start a grandchild, then block."""\n'
    '    child = subprocess.Popen(["sleep", "60"])\n'
    '    open(pidfile, "w").write(f"{os.getpid()} {child.pid}")\n'
    "    time.sleep(60)\n"
    "    return 1\n\n"
    "# notebook-to-api: background\n"
    "def quick_job(x: int) -> int:\n"
    '    """Finish immediately."""\n'
    "    return x\n\n"
    "# notebook-to-api: background\n"
    "def boom_job() -> int:\n"
    '    """Fail immediately."""\n'
    '    raise ValueError("boom")\n'
)


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@pytest.fixture
def served(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "cancel_app"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "cancel-key")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_EXECUTION", "1")
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_MAX_PROCESSES", "2")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("cancel_app.app")
    pids = []
    with TestClient(module.app) as client:
        yield module, client, tmp_path, pids
    for pid in pids:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _start(module, client, tmp_path, name, pids):
    """Submit hang_job in a thread (TestClient runs background work inline)."""
    pidfile = tmp_path / name
    holder = {}
    before = set(module.TASKS)

    def submit():
        holder["resp"] = client.post("/hang_job", json={"pidfile": str(pidfile)}, headers=HEADERS)

    thread = threading.Thread(target=submit, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text().count(" ")):
        time.sleep(0.05)
    found = list(map(int, pidfile.read_text().split()))
    pids.extend(found)
    task_id = next(t for t in module.TASKS if t not in before)
    return thread, found, task_id


def test_cancel_running_task_kills_child_and_group_and_frees_capacity(served):
    module, client, tmp_path, pids = served
    thread, found, task_id = _start(module, client, tmp_path, "a", pids)
    assert all(_alive(p) for p in found)

    began = time.monotonic()
    resp = client.post(f"/tasks/{task_id}/cancel", headers=HEADERS)
    thread.join(20)

    assert resp.status_code == 200 and resp.json() == {"task_id": task_id, "status": "cancelled"}
    assert not thread.is_alive() and time.monotonic() - began < 10
    assert not any(_alive(p) for p in found)  # the child AND its grandchild are gone
    assert client.get(f"/tasks/{task_id}", headers=HEADERS).json()["status"] == "cancelled"
    assert task_id not in module._TASK_CANCEL
    # Capacity is back: both slots can be taken again.
    assert module._ISOLATE_SLOTS.acquire(blocking=False) and module._ISOLATE_SLOTS.acquire(blocking=False)
    module._ISOLATE_SLOTS.release()
    module._ISOLATE_SLOTS.release()


def test_repeated_cancel_is_deterministic_and_terminal_states_are_protected(served):
    module, client, tmp_path, pids = served
    thread, found, task_id = _start(module, client, tmp_path, "b", pids)
    first = client.post(f"/tasks/{task_id}/cancel", headers=HEADERS)
    second = client.post(f"/tasks/{task_id}/cancel", headers=HEADERS)
    thread.join(20)
    assert first.json() == second.json() == {"task_id": task_id, "status": "cancelled"}
    assert client.get(f"/tasks/{task_id}", headers=HEADERS).json()["status"] == "cancelled"

    done = client.post("/quick_job", json={"x": 3}, headers=HEADERS).json()["task_id"]
    failed = client.post("/boom_job", json={}, headers=HEADERS).json()["task_id"]
    assert client.post(f"/tasks/{done}/cancel", headers=HEADERS).status_code == 409
    assert client.post(f"/tasks/{failed}/cancel", headers=HEADERS).status_code == 409
    assert client.get(f"/tasks/{done}", headers=HEADERS).json()["status"] == "completed"
    assert client.get(f"/tasks/{failed}", headers=HEADERS).json()["status"] == "failed"
    assert client.post("/tasks/nope/cancel", headers=HEADERS).status_code == 404


def test_cancelling_one_task_leaves_a_concurrent_task_running(served):
    module, client, tmp_path, pids = served
    t1, found1, id1 = _start(module, client, tmp_path, "c1", pids)
    t2, found2, id2 = _start(module, client, tmp_path, "c2", pids)
    assert id1 != id2
    client.post(f"/tasks/{id1}/cancel", headers=HEADERS)
    t1.join(20)
    assert not any(_alive(p) for p in found1)
    assert all(_alive(p) for p in found2)
    assert client.get(f"/tasks/{id2}", headers=HEADERS).json()["status"] == "processing"
    client.post(f"/tasks/{id2}/cancel", headers=HEADERS)
    t2.join(20)
    assert not any(_alive(p) for p in found2)


def test_cancel_queued_task_never_runs_and_unkillable_running_task_is_refused(served):
    module, client, _, _ = served
    module.TASKS["queued"] = {"status": "processing", "created_at": 1.0}
    assert client.post("/tasks/queued/cancel", headers=HEADERS).json()["status"] == "cancelled"

    def must_not_run():
        raise AssertionError("cancelled task executed")

    asyncio.run(module._run_background_task(must_not_run, "queued"))
    assert module.TASKS["queued"]["status"] == "cancelled"

    module.TASKS["stuck"] = {"status": "processing", "created_at": 1.0, "started_at": 2.0}
    module._TASK_CANCEL["stuck"] = {"event": threading.Event(), "reaped": threading.Event(), "scope": None, "pid": None, "killable": False}
    resp = client.post("/tasks/stuck/cancel", headers=HEADERS)
    module._TASK_CANCEL.pop("stuck")
    assert resp.status_code == 409 and module.TASKS["stuck"]["status"] == "processing"
