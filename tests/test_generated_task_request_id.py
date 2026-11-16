"""A background task records the X-Request-ID of the request that submitted it, so
a failed task can be matched with the access log line and error log that started it."""
import importlib
import sys
import time

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

HEADERS = {"X-API-Key": "k", "X-Request-ID": "req-abc-123"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    src = "def train_ok(x: int) -> int:\n    return x\ndef train_fail(x: int) -> int:\n    raise ValueError('nope')\n"
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(src)]), str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "task_request_id_app"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "k")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("task_request_id_app.app")
    with TestClient(module.app) as client:
        yield client
    for name in [m for m in sys.modules if m.startswith("task_request_id_app")]:
        del sys.modules[name]


def _finished(client, task_id):
    for _ in range(100):
        task = client.get(f"/tasks/{task_id}", headers=HEADERS).json()
        if task["status"] != "processing":
            return task
        time.sleep(0.05)
    raise AssertionError("task never finished")


@pytest.mark.parametrize("name,status", [("train_ok", "completed"), ("train_fail", "failed")])
def test_task_carries_the_submitting_request_id(client, name, status):
    task_id = client.post(f"/{name}", json={"x": 1}, headers=HEADERS).json()["task_id"]
    task = _finished(client, task_id)
    assert task["status"] == status and task["request_id"] == "req-abc-123"


def test_request_id_is_generated_and_bounded_when_absent_or_huge(client):
    headers = {"X-API-Key": "k", "X-Request-ID": "x" * 500}
    task = _finished(client, client.post("/train_ok", json={"x": 1}, headers=headers).json()["task_id"])
    assert task["request_id"] == "x" * 128
    task_id = client.post("/train_ok", json={"x": 1}, headers={"X-API-Key": "k"}).json()["task_id"]
    assert len(_finished(client, task_id)["request_id"]) == 36  # generated uuid
