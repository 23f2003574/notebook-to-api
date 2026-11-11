import importlib

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

SOURCE = "# notebook-to-api: background\ndef work(x: int) -> int:\n    return x * 2\n"
HEADERS = {"X-API-Key": "started-key"}


def _module(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "started_app"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "started-key")
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module("started_app.app")


def test_completed_task_records_when_execution_actually_started(tmp_path, monkeypatch):
    client = TestClient(_module(tmp_path, monkeypatch).app)
    task_id = client.post("/work", json={"x": 4}, headers=HEADERS).json()["task_id"]

    task = client.get(f"/tasks/{task_id}", headers=HEADERS).json()

    assert task["status"] == "completed" and task["result"] == 8
    assert task["created_at"] <= task["started_at"] <= task["finished_at"]


def test_task_still_waiting_for_a_slot_has_no_started_at(tmp_path, monkeypatch):
    module = _module(tmp_path, monkeypatch)
    module.TASKS["waiting"] = {"status": "processing", "created_at": 1.0}
    client = TestClient(module.app)

    task = client.get("/tasks/waiting", headers=HEADERS).json()

    assert task["status"] == "processing" and "started_at" not in task
