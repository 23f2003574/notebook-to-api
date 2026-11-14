"""Task state is saved as each transition happens, not only when the run function exits."""
import importlib
import json
import sys

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

HEADERS = {"X-API-Key": "transition-key"}
URL = "https://example.com/hook"
SOURCE = (
    "# notebook-to-api: background\ndef work(x: int) -> int:\n    return x * 2\n\n"
    "# notebook-to-api: background\ndef boom() -> int:\n    raise ValueError('boom')\n"
)


def _boot(tmp_path, monkeypatch):
    if not (tmp_path / "transition_app").exists():
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
        nbformat.write(nb, str(tmp_path / "nb.ipynb"))
        compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "transition_app"))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "transition-key")
    monkeypatch.setenv("NOTEBOOK_API_TASK_STORE_PATH", str(tmp_path / "store.json"))
    for key in [k for k in sys.modules if k.startswith("transition_app")]:
        del sys.modules[key]
    module = importlib.import_module("transition_app.app")
    module._is_unsafe_webhook_host = lambda url: False  # no DNS in tests; delivery is faked too
    return module, tmp_path / "store.json"


def _stored(store):
    return json.loads(store.read_text())["tasks"]


def _record_store_during_delivery(module, store, seen, delivered=True):
    def fake(callback_url, payload):
        seen.append(_stored(store).get(payload["task_id"]))
        return {"delivered": delivered}
    module._deliver_task_webhook = fake


def test_finished_task_is_on_disk_before_webhook_delivery(tmp_path, monkeypatch):
    module, store = _boot(tmp_path, monkeypatch)
    seen = []
    _record_store_during_delivery(module, store, seen)
    client = TestClient(module.app)
    ok = client.post(f"/work?callback_url={URL}", json={"x": 4}, headers=HEADERS).json()["task_id"]
    bad = client.post(f"/boom?callback_url={URL}", json={}, headers=HEADERS).json()["task_id"]

    done, failed = seen  # what a crash during delivery would have left behind
    assert done["status"] == "completed" and done["result"] == 8
    assert failed["status"] == "failed" and "boom" in failed["error"]
    assert _stored(store)[ok]["webhook"]["delivered"] is True
    assert _stored(store)[bad]["webhook"]["delivered"] is True


def test_webhook_outcome_and_redelivery_survive_restart(tmp_path, monkeypatch):
    module, store = _boot(tmp_path, monkeypatch)
    _record_store_during_delivery(module, store, [], delivered=False)
    client = TestClient(module.app)
    task = client.post(f"/work?callback_url={URL}", json={"x": 1}, headers=HEADERS).json()["task_id"]
    assert _stored(store)[task]["webhook"]["delivered"] is False

    _record_store_during_delivery(module, store, [], delivered=True)
    assert client.post(f"/tasks/{task}/redeliver-webhook", headers=HEADERS).status_code == 200
    saved = _stored(store)[task]
    assert saved["webhook"]["delivered"] is True and saved["webhook_redelivery_count"] == 1

    restarted = TestClient(_boot(tmp_path, monkeypatch)[0].app)
    got = restarted.get(f"/tasks/{task}", headers=HEADERS).json()
    assert got["status"] == "completed" and got["result"] == 2
    assert got["webhook_redelivery_count"] == 1 and got["webhook"]["delivered"] is True
