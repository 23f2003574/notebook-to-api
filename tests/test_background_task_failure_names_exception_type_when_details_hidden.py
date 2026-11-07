import importlib
import time

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

SOURCE = (
    "def train_fail(n: int) -> int:\n"
    '    raise KeyError("sk-live-SUPERSECRET123456")\n'
)
HEADERS = {"X-API-Key": "task-key"}


def test_failed_task_reports_the_exception_class_but_not_its_message_when_hidden(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "task_hidden_pkg"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "task-key")
    monkeypatch.setenv("NOTEBOOK_API_HIDE_ERROR_DETAILS", "true")
    monkeypatch.syspath_prepend(str(tmp_path))
    client = TestClient(importlib.import_module("task_hidden_pkg.app").app)

    task_id = client.post("/train_fail", json={"n": 1}, headers=HEADERS).json()["task_id"]
    status = {}
    for _ in range(50):
        status = client.get(f"/tasks/{task_id}", headers=HEADERS).json()
        if status["status"] == "failed":
            break
        time.sleep(0.05)

    assert status["status"] == "failed"
    assert status["error"].startswith("KeyError:")
    assert "SUPERSECRET" not in status["error"]
