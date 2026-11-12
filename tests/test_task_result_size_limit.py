"""Oversized background-task results are never stored; the task fails clearly."""
import importlib
import sys

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

HEADERS = {"X-API-Key": "size-key"}
SOURCE = (
    "# notebook-to-api: background\n"
    "def make(n: int) -> str:\n"
    '    return "x" * n\n'
)


def _client(tmp_path, monkeypatch, limit):
    if not (tmp_path / "size_app").exists():
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
        nbformat.write(nb, str(tmp_path / "nb.ipynb"))
        compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "size_app"))
        monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "size-key")
    monkeypatch.setenv("NOTEBOOK_API_MAX_TASK_RESULT_BYTES", str(limit))
    for name in [k for k in sys.modules if k.startswith("size_app")]:
        del sys.modules[name]
    module = importlib.import_module("size_app.app")
    return module, TestClient(module.app)


def _run(client, endpoint, body):
    task_id = client.post(endpoint, json=body, headers=HEADERS).json()["task_id"]
    return task_id, client.get(f"/tasks/{task_id}", headers=HEADERS).json()


def test_result_within_limit_is_stored_and_retrievable(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch, 100)
    _, task = _run(client, "/make", {"n": 50})  # 52 bytes as JSON
    assert task["status"] == "completed" and task["result"] == "x" * 50


def test_result_over_limit_fails_terminally_and_is_not_retained(tmp_path, monkeypatch):
    module, client = _client(tmp_path, monkeypatch, 100)
    task_id, task = _run(client, "/make", {"n": 99})  # 101 bytes as JSON
    assert task["status"] == "failed" and "result" not in task
    assert "NOTEBOOK_API_MAX_TASK_RESULT_BYTES=100" in task["error"] and "not stored" in task["error"]
    assert task["finished_at"] >= task["created_at"] and "result" not in module.TASKS[task_id]


def test_limit_is_configurable_and_zero_disables_it(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch, 20)
    assert _run(client, "/make", {"n": 30})[1]["status"] == "failed"
    _, client = _client(tmp_path, monkeypatch, 40)
    assert _run(client, "/make", {"n": 30})[1]["status"] == "completed"
    _, client = _client(tmp_path, monkeypatch, 0)
    assert _run(client, "/make", {"n": 5000})[1]["status"] == "completed"


def test_serialization_failure_during_size_check_fails_instead_of_sticking(tmp_path, monkeypatch):
    module, client = _client(tmp_path, monkeypatch, 100)

    def broken(value, limit):
        raise TypeError("cannot encode")

    monkeypatch.setattr(module, "_json_size_exceeds", broken)
    _, task = _run(client, "/make", {"n": 5})
    assert task["status"] == "failed" and "not JSON-serializable: cannot encode" in task["error"]


def test_size_check_stops_early_and_matches_the_returned_encoding(tmp_path, monkeypatch):
    module, _ = _client(tmp_path, monkeypatch, 100)
    assert module._json_size_exceeds({"a": "é" * 10}, 100) is False
    assert module._json_size_exceeds({"a": "é" * 10}, 20) is True  # utf-8: 2 bytes per char
    assert module._json_size_exceeds([1, 2, 3], 7) is False and module._json_size_exceeds([1, 2, 3], 6) is True
