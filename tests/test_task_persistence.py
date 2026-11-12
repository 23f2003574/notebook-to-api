"""Task metadata survives a restart; interrupted tasks are failed, never resumed."""
import importlib
import json
import os
import sys
import time

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

HEADERS = {"X-API-Key": "persist-key"}
SOURCE = (
    "# notebook-to-api: background\n"
    "def work(x: int) -> int:\n"
    "    return x * 2\n\n"
    "# notebook-to-api: background\n"
    "def boom() -> int:\n"
    '    raise ValueError("boom")\n'
)


def _boot(tmp_path, monkeypatch, store="store.json", ttl=None):
    """Compile once, then (re)import the app: re-importing is a restart."""
    if not (tmp_path / "persist_app").exists():
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
        nbformat.write(nb, str(tmp_path / "nb.ipynb"))
        compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "persist_app"))
        monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "persist-key")
    for name, value in (("NOTEBOOK_API_TASK_STORE_PATH", str(tmp_path / store) if store else None),
                        ("NOTEBOOK_API_TASK_TTL_SECONDS", ttl)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    for key in [k for k in sys.modules if k.startswith("persist_app")]:
        del sys.modules[key]
    return importlib.import_module("persist_app.app")


def test_completed_and_failed_tasks_and_results_survive_restart(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    client = TestClient(module.app)
    done = client.post("/work", json={"x": 4}, headers=HEADERS).json()["task_id"]
    failed = client.post("/boom", json={}, headers=HEADERS).json()["task_id"]

    data = json.loads((tmp_path / "store.json").read_text())
    assert data["version"] == 1 and data["tasks"][done]["result"] == 8
    assert all("_replay" not in t for t in data["tasks"].values())  # request args are never written
    assert not list(tmp_path.glob("store.json.*.tmp"))  # atomic replace leaves no partial file

    client = TestClient(_boot(tmp_path, monkeypatch).app)
    task = client.get(f"/tasks/{done}", headers=HEADERS).json()
    assert task["status"] == "completed" and task["result"] == 8
    assert client.get(f"/tasks/{failed}", headers=HEADERS).json()["status"] == "failed"


def test_interrupted_tasks_fail_with_reason_and_cancelled_is_preserved(tmp_path, monkeypatch):
    now = time.time()
    store = {"version": 1, "tasks": {
        "p": {"status": "processing", "created_at": now, "started_at": now},
        "q": {"status": "queued", "created_at": now},
        "c": {"status": "cancelled", "created_at": now, "finished_at": now},
        "bad": "not-a-task",
    }}
    (tmp_path / "store.json").write_text(json.dumps(store))
    module = _boot(tmp_path, monkeypatch)
    client = TestClient(module.app)

    for task_id in ("p", "q"):
        task = client.get(f"/tasks/{task_id}", headers=HEADERS).json()
        assert task["status"] == "failed" and task["interrupted_by_restart"] is True
        assert task["error"].startswith("interrupted_by_restart") and task["finished_at"] >= now
    assert client.get("/tasks/c", headers=HEADERS).json()["status"] == "cancelled"
    assert client.get("/tasks/bad", headers=HEADERS).status_code == 404
    # The recovered state was written back, so a second restart sees the same thing.
    assert json.loads((tmp_path / "store.json").read_text())["tasks"]["p"]["status"] == "failed"


def test_malformed_store_is_quarantined_not_deleted(tmp_path, monkeypatch):
    (tmp_path / "store.json").write_text("{not json")
    module = _boot(tmp_path, monkeypatch)
    assert dict(module.TASKS) == {}
    kept = list(tmp_path.glob("store.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{not json"


def test_eviction_after_recovery_removes_expired_tasks_from_disk(tmp_path, monkeypatch):
    old = time.time() - 100
    store = {"version": 1, "tasks": {
        "old": {"status": "completed", "created_at": old, "finished_at": old, "result": 1},
        "new": {"status": "completed", "created_at": time.time(), "finished_at": time.time(), "result": 2},
    }}
    (tmp_path / "store.json").write_text(json.dumps(store))
    module = _boot(tmp_path, monkeypatch, ttl="50")
    TestClient(module.app).post("/work", json={"x": 1}, headers=HEADERS)  # submitting evicts expired tasks
    saved = json.loads((tmp_path / "store.json").read_text())["tasks"]
    assert "old" not in saved and "new" in saved


def test_persistence_is_off_by_default(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch, store=None)
    client = TestClient(module.app)
    assert client.post("/work", json={"x": 1}, headers=HEADERS).json()["status"] == "processing"
    assert module.TASK_STORE_PATH == "" and not list(tmp_path.glob("*.json"))
    assert type(module.TASKS).__name__ == "_TaskRegistry" and os.listdir(tmp_path)
