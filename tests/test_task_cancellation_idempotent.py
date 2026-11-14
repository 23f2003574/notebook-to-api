"""Cancelling is repeatable and concurrent-safe: one outcome, one cleanup, answered after reaping."""
import asyncio
import importlib
import json
import sys
import threading

import httpx
import nbformat
import pytest

from backend.compiler import compile_notebook

H = {"X-API-Key": "cancel-key"}
SOURCE = "# notebook-to-api: background\ndef work(n: int) -> int:\n    return n\n"


class CountingScope:
    def __init__(self):
        self.cancels = 0

    def cancel(self):
        self.cancels += 1


class CountingEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.sets = 0

    def set(self):
        self.sets += 1
        super().set()


@pytest.fixture
def mod(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "cancel_app"))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "cancel-key")
    monkeypatch.setenv("NOTEBOOK_API_TASK_STORE_PATH", str(tmp_path / "s.json"))
    for key in [k for k in sys.modules if k.startswith("cancel_app")]:
        del sys.modules[key]
    module = importlib.import_module("cancel_app.app")
    module.store = tmp_path / "s.json"
    return module


def _running_task(mod):
    """A processing task whose isolated child (pid 1234) is still being reaped."""
    mod.TASKS["t"] = {"status": "processing", "created_at": 1.0, "started_at": 2.0}
    entry = {"event": CountingEvent(), "reaped": threading.Event(), "scope": CountingScope(),
             "pid": 1234, "killable": True}
    mod._TASK_CANCEL["t"] = entry
    return entry


def _run(coro):
    return asyncio.run(coro)


def test_concurrent_cancels_both_wait_for_reaping_and_clean_up_once(mod):
    entry = _running_task(mod)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            first = asyncio.create_task(c.post("/tasks/t/cancel", headers=H))
            while mod.TASKS["t"]["status"] != "cancelled":
                await asyncio.sleep(0)
            second = asyncio.create_task(c.post("/tasks/t/cancel", headers=H))
            done, _ = await asyncio.wait({first, second}, timeout=0.3)
            blocked_until_reaped = not done  # neither may answer while the child is alive
            entry["reaped"].set()
            return blocked_until_reaped, await first, await second

    blocked, r1, r2 = _run(scenario())
    assert blocked
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json() == {"task_id": "t", "status": "cancelled"}
    assert entry["event"].sets == 1 and entry["scope"].cancels == 1  # cleanup requested once
    assert json.loads(mod.store.read_text())["tasks"]["t"]["status"] == "cancelled"


def test_repeated_cancel_after_finish_is_idempotent(mod):
    mod.TASKS["t"] = {"status": "processing", "created_at": 1.0}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            return [await c.post("/tasks/t/cancel", headers=H) for _ in range(3)]

    responses = _run(scenario())
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert {r.text for r in responses} == {responses[0].text}
    finished = mod.TASKS["t"]["finished_at"]
    _run(scenario())
    assert mod.TASKS["t"]["finished_at"] == finished  # a repeat never rewrites the outcome


@pytest.mark.parametrize("final, fields", [("completed", {"result": 7}), ("failed", {"error": "boom"})])
def test_cancelling_a_finished_task_is_refused_and_leaves_it_untouched(mod, final, fields):
    mod.TASKS["t"] = {"status": "processing", "created_at": 1.0}
    mod._transition_task("t", final, **fields)
    before = dict(mod.TASKS["t"])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            return await c.post("/tasks/t/cancel", headers=H)

    r = _run(scenario())
    assert r.status_code == 409 and final in r.json()["detail"]
    assert mod.TASKS["t"] == before
    assert json.loads(mod.store.read_text())["tasks"]["t"]["status"] == final


def test_completion_or_failure_after_cancel_cannot_change_the_outcome(mod):
    _running_task(mod)["reaped"].set()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            return await c.post("/tasks/t/cancel", headers=H)

    assert _run(scenario()).status_code == 200
    assert mod._transition_task("t", "completed", result=1) is False
    assert mod._transition_task("t", "failed", error="late") is False
    task = mod.TASKS["t"]
    assert task["status"] == "cancelled" and "result" not in task and "error" not in task
    assert json.loads(mod.store.read_text())["tasks"]["t"] == task


def test_unknown_task_is_404(mod):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            return await c.post("/tasks/nope/cancel", headers=H)

    assert _run(scenario()).status_code == 404
