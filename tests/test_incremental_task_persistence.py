"""Saving a task writes only that task: no full-file rewrite per update, and the store still recovers."""
import json
import os
import threading
import time

from fastapi.testclient import TestClient

from tests.task_store import read_tasks
from tests.test_task_persistence import HEADERS, _boot


def _journal(tmp_path):
    path = tmp_path / "store.json.journal"
    return path.read_text() if path.exists() else ""


def test_one_update_appends_one_small_record_regardless_of_how_many_tasks_exist(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    client = TestClient(module.app)
    sizes = []
    for batch in (20, 200, 600):
        for _ in range(batch):
            client.post("/work", json={"x": 1}, headers=HEADERS)
        before = len(_journal(tmp_path).encode())
        snapshot_before = os.stat(tmp_path / "store.json").st_mtime_ns
        module.TASKS[next(iter(module.TASKS))]["marker"] = len(sizes)
        module._persist_tasks()
        sizes.append(len(_journal(tmp_path).encode()) - before)
        assert os.stat(tmp_path / "store.json").st_mtime_ns == snapshot_before  # the snapshot was not rewritten
    assert all(0 < size < 2000 for size in sizes) and max(sizes) < 2 * min(sizes), sizes


def test_a_save_serializes_only_changed_tasks(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    client = TestClient(module.app)
    for _ in range(50):
        client.post("/work", json={"x": 1}, headers=HEADERS)
    time.sleep(0.2)
    seen = []
    real = module._task_record
    monkeypatch.setattr(module, "_task_record", lambda task: (seen.append(1), real(task))[1])
    client.post("/work", json={"x": 2}, headers=HEADERS)
    time.sleep(0.3)
    assert 1 <= len(seen) <= 6, len(seen)  # the new task and its transitions, never all 50


def test_tasks_recover_after_a_restart_and_interrupted_ones_are_failed(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    client = TestClient(module.app)
    done = client.post("/work", json={"x": 4}, headers=HEADERS).json()["task_id"]
    time.sleep(0.3)
    module.TASKS["stuck"] = {"status": "processing", "created_at": time.time()}
    removed = client.post("/work", json={"x": 1}, headers=HEADERS).json()["task_id"]
    time.sleep(0.3)
    del module.TASKS[removed]
    restarted = _boot(tmp_path, monkeypatch)
    assert restarted.TASKS[done]["result"] == 8 and restarted.TASKS[done]["status"] == "completed"
    assert restarted.TASKS["stuck"]["status"] == "failed" and restarted.TASKS["stuck"]["interrupted_by_restart"] is True
    assert removed not in restarted.TASKS
    assert json.loads((tmp_path / "store.json").read_text())["epoch"] >= 1  # startup folds the journal into a snapshot
    assert _journal(tmp_path) == ""


def test_a_torn_or_damaged_journal_line_costs_only_that_line(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    module.TASKS["keep"] = {"status": "completed", "result": 1, "created_at": 1.0}
    module.TASKS["also"] = {"status": "completed", "result": 2, "created_at": 2.0}
    with open(tmp_path / "store.json.journal", "a") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"e": json.loads((tmp_path / "store.json").read_text()).get("epoch", 0), "op": "put", "id": "last", "task": {"status": "completed", "result": 3}})[:30])  # cut mid-write
    restarted = _boot(tmp_path, monkeypatch)
    assert restarted.TASKS["keep"]["result"] == 1 and restarted.TASKS["also"]["result"] == 2 and "last" not in restarted.TASKS


def test_journal_lines_from_an_older_epoch_never_overwrite_a_newer_snapshot(tmp_path, monkeypatch):
    (tmp_path / "store.json").write_text(json.dumps({"version": 1, "epoch": 5, "tasks": {"t": {"status": "completed", "result": "new"}}}))
    (tmp_path / "store.json.journal").write_text(
        json.dumps({"e": 4, "op": "put", "id": "t", "task": {"status": "processing", "result": "old"}}) + "\n"
        + json.dumps({"e": 4, "op": "del", "id": "t"}) + "\n"
    )
    module = _boot(tmp_path, monkeypatch)
    assert module.TASKS["t"]["result"] == "new"


def test_a_pre_journal_store_is_migrated(tmp_path, monkeypatch):
    (tmp_path / "store.json").write_text(json.dumps({"version": 1, "tasks": {
        "old1": {"status": "completed", "result": 1, "created_at": time.time()},
        "old2": {"status": "failed", "error": "x", "created_at": time.time()},
    }}))
    module = _boot(tmp_path, monkeypatch)
    assert set(module.TASKS) == {"old1", "old2"}
    module.TASKS["new"] = {"status": "completed", "result": 3, "created_at": time.time()}
    assert set(read_tasks(tmp_path / "store.json")) == {"old1", "old2", "new"}
    assert set(_boot(tmp_path, monkeypatch).TASKS) == {"old1", "old2", "new"}


def test_concurrent_submissions_are_all_persisted_exactly_once(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    ids = []
    lock = threading.Lock()

    def worker(n):
        for i in range(40):
            task_id = f"t{n}-{i}"
            module.TASKS[task_id] = {"status": "completed", "result": i, "created_at": time.time()}
            with lock:
                ids.append(task_id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    stored = read_tasks(tmp_path / "store.json")
    assert set(stored) == set(ids) and len(stored) == 320
    lines = [line for line in _journal(tmp_path).splitlines() if line]
    assert all(json.loads(line)["op"] in ("put", "del") for line in lines)  # no interleaved or partial lines


def test_the_journal_stays_bounded_by_compaction(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    module.TASKS["a"] = {"status": "completed", "result": 0, "created_at": 1.0}
    for i in range(1500):
        module.TASKS["a"]["result"] = i
        module._persist_tasks()
    assert module._TASK_JOURNAL["records"] <= 1000
    assert read_tasks(tmp_path / "store.json")["a"]["result"] == 1499
    assert json.loads((tmp_path / "store.json").read_text())["epoch"] >= 1
    assert read_tasks(tmp_path / "store.json")["a"]["result"] == _boot(tmp_path, monkeypatch).TASKS["a"]["result"]


def test_a_failed_write_is_retried_with_the_next_save(tmp_path, monkeypatch):
    module = _boot(tmp_path, monkeypatch)
    module.TASKS["a"] = {"status": "completed", "result": 1, "created_at": 1.0}
    real_open = open

    def broken(path, *args, **kwargs):
        if str(path).endswith(".journal") and "a" in (args[0] if args else kwargs.get("mode", "")):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", broken)
    module.TASKS["b"] = {"status": "completed", "result": 2, "created_at": 2.0}  # save fails, is logged, does not raise
    monkeypatch.undo()
    module.TASKS["c"] = {"status": "completed", "result": 3, "created_at": 3.0}
    assert {"a", "b", "c"} <= set(read_tasks(tmp_path / "store.json"))
