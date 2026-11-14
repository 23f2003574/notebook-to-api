"""A task leaves "processing" once; final states are never overwritten and stay consistent."""
import asyncio
import importlib
import json
import sys

import httpx
import nbformat
import pytest

from backend.compiler import compile_notebook

H = {"X-API-Key": "life-key"}
SOURCE = (
    "# notebook-to-api: background\ndef quick(n: int) -> int:\n    return n\n\n"
    "# notebook-to-api: background\nasync def hang(n: int) -> int:\n"
    "    import asyncio\n    await asyncio.sleep(n)\n    return n\n"
)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "life_app"))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "life-key")
    monkeypatch.setenv("NOTEBOOK_API_TASK_STORE_PATH", str(tmp_path / "s.json"))
    for key in [k for k in sys.modules if k.startswith("life_app")]:
        del sys.modules[key]
    module = importlib.import_module("life_app.app")
    module._is_unsafe_webhook_host = lambda url: False
    module.store = tmp_path / "s.json"
    return module


def _stored(mod, tid):
    return json.loads(mod.store.read_text())["tasks"][tid]


@pytest.mark.parametrize("final, fields", [("completed", {"result": 3}), ("failed", {"error": "x"}), ("cancelled", {})])
def test_processing_moves_once_to_a_final_state_with_consistent_fields_and_is_saved(mod, final, fields):
    mod.TASKS["t"] = {"status": "processing", "created_at": 1.0}
    assert mod._transition_task("t", final, **fields) is True
    task = mod.TASKS["t"]
    assert task["status"] == final and task["finished_at"] >= task["created_at"]
    assert all(task[k] == v for k, v in fields.items())
    assert _stored(mod, "t") == {**task}


@pytest.mark.parametrize("first", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("second", ["completed", "failed", "cancelled"])
def test_final_states_cannot_be_overwritten(mod, first, second):
    mod.TASKS["t"] = {"status": "processing", "created_at": 1.0}
    mod._transition_task("t", first, **({"result": 1} if first == "completed" else {"error": "e"} if first == "failed" else {}))
    before = dict(mod.TASKS["t"])
    assert mod._transition_task("t", second, result=99, error="late") is False
    assert mod.TASKS["t"] == before and "late" not in json.dumps(_stored(mod, "t"))


def test_unknown_task_and_unknown_status_are_refused(mod):
    assert mod._transition_task("missing", "completed", result=1) is False
    mod.TASKS["t"] = {"status": "processing"}
    assert mod._transition_task("t", "queued") is False and mod.TASKS["t"]["status"] == "processing"


def test_a_failing_webhook_delivery_does_not_turn_a_completed_task_into_a_failure(mod):
    calls = []

    def boom(url, payload):
        calls.append(payload["status"])
        raise RuntimeError("delivery exploded")

    mod._deliver_task_webhook = boom

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            return await c.post("/quick?callback_url=https://example.com/h", json={"n": 5}, headers=H)

    assert asyncio.run(run()).status_code == 200
    task = next(iter(mod.TASKS.values()))
    assert task["status"] == "completed" and task["result"] == 5 and "error" not in task
    assert calls == ["completed"]  # no contradictory second "failed" notification
    assert task["webhook"]["delivered"] is False
    assert _stored(mod, next(iter(mod.TASKS)))["status"] == "completed"


def test_cancel_then_late_completion_keeps_cancelled_and_completed_then_cancel_is_refused(mod):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://t") as c:
            post = asyncio.create_task(c.post("/hang", json={"n": 30}, headers=H))
            while not any(t.get("started_at") for t in mod.TASKS.values()):
                await asyncio.sleep(0)
            tid = next(iter(mod.TASKS))
            assert (await c.post(f"/tasks/{tid}/cancel", headers=H)).status_code == 200
            await asyncio.wait_for(post, 20)
            assert mod._transition_task(tid, "completed", result=30) is False  # late completion
            cancelled = (await c.get(f"/tasks/{tid}", headers=H)).json()
            await c.post("/quick", json={"n": 1}, headers=H)
            done = next(k for k, t in mod.TASKS.items() if t["status"] == "completed")
            refused = await c.post(f"/tasks/{done}/cancel", headers=H)
            return tid, cancelled, done, refused.status_code

    tid, cancelled, done, refused = asyncio.run(run())
    assert cancelled["status"] == "cancelled" and "result" not in cancelled and "error" not in cancelled
    assert _stored(mod, tid)["status"] == "cancelled"
    assert refused == 409 and mod.TASKS[done]["status"] == "completed" and mod.TASKS[done]["result"] == 1
