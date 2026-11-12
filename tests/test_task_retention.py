"""Terminal tasks expire after the TTL, on read and in the background; live ones never do."""
import asyncio
import importlib
import json
import sys
import threading
import time

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

HEADERS = {"X-API-Key": "ret-key"}
SOURCE = "# notebook-to-api: background\ndef work(x: int) -> int:\n    return x\n"


def _boot(tmp_path, monkeypatch, store=None):
    if not (tmp_path / "ret_app").exists():
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
        nbformat.write(nb, str(tmp_path / "nb.ipynb"))
        compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "ret_app"))
        monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "ret-key")
    monkeypatch.setenv("NOTEBOOK_API_TASK_TTL_SECONDS", "100")
    if store:
        monkeypatch.setenv("NOTEBOOK_API_TASK_STORE_PATH", str(store))
    else:
        monkeypatch.delenv("NOTEBOOK_API_TASK_STORE_PATH", raising=False)
    for name in [k for k in sys.modules if k.startswith("ret_app")]:
        del sys.modules[name]
    return importlib.import_module("ret_app.app")


def _seed(module):
    now = time.time()
    old = now - 1000
    for tid, status in (("done_old", "completed"), ("failed_old", "failed"), ("cancelled_old", "cancelled")):
        module.TASKS[tid] = {"status": status, "created_at": old, "finished_at": old, "result": 1}
    module.TASKS["done_new"] = {"status": "completed", "created_at": now, "finished_at": now, "result": 2}
    module.TASKS["processing_old"] = {"status": "processing", "created_at": old}
    module.TASKS["queued_old"] = {"status": "queued", "created_at": old}


def test_unexpired_terminal_task_is_retrievable_and_expired_ones_are_gone(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    _seed(module)
    client = TestClient(module.app)
    assert client.get("/tasks/done_new", headers=HEADERS).json()["result"] == 2
    for tid in ("done_old", "failed_old", "cancelled_old"):
        assert client.get(f"/tasks/{tid}", headers=HEADERS).status_code == 404  # no new submission needed


def test_listing_never_returns_expired_tasks_and_live_tasks_survive_any_age(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    _seed(module)
    assert TestClient(module.app).get("/tasks", headers=HEADERS).status_code == 200
    assert set(module.TASKS) == {"done_new", "processing_old", "queued_old"}


def test_background_loop_evicts_without_any_request(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    _seed(module)
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) > 1:
            raise asyncio.CancelledError
        await asyncio.sleep(0)

    monkeypatch.setattr(module.anyio, "sleep", fake_sleep)
    try:
        asyncio.run(module._evict_loop())
    except asyncio.CancelledError:
        pass
    assert set(module.TASKS) == {"done_new", "processing_old", "queued_old"} and calls[0] == 25  # TTL/4


def test_cleanup_task_follows_the_app_lifecycle(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    events = []

    async def stub_loop():
        events.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    monkeypatch.setattr(module, "_evict_loop", stub_loop)
    with TestClient(module.app) as client:
        client.get("/health")
        assert events == ["started"]
    assert events == ["started", "cancelled"]


def test_concurrent_updates_and_eviction_keep_the_registry_consistent(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    errors = []
    stop = threading.Event()

    def evictor():
        try:
            while not stop.is_set():
                module._evict_expired_tasks()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def writer():
        try:
            for i in range(300):
                tid = f"live{i}"
                module.TASKS[tid] = {"status": "processing", "created_at": time.time() - 1000}
                module.TASKS[tid]["status"] = "completed"
                module.TASKS[tid]["finished_at"] = time.time()
                module.TASKS[f"old{i}"] = {"status": "failed", "created_at": 1.0, "finished_at": 1.0}
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=evictor) for _ in range(2)] + [threading.Thread(target=writer)]
    for t in threads:
        t.start()
    threads[-1].join(30)
    stop.set()
    for t in threads[:-1]:
        t.join(10)
    module._evict_expired_tasks()
    assert not errors
    assert {k for k in module.TASKS} == {f"live{i}" for i in range(300)}  # fresh kept, all old ones gone


def test_eviction_is_written_through_to_the_persisted_store(tmp_path, monkeypatch):
    store = tmp_path / "store.json"
    module = _boot(tmp_path, monkeypatch, store=store)
    _seed(module)
    TestClient(module.app).get("/tasks/done_new", headers=HEADERS)
    saved = set(json.loads(store.read_text())["tasks"])
    assert saved == {"done_new", "processing_old", "queued_old"}
