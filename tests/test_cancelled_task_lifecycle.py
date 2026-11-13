"""`cancelled` is a first-class task status: filter, counts, metrics and cleanup."""
import importlib
import time

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook
from backend.exporters.sdk_generator import generate_python_sdk

HEADERS = {"X-API-Key": "lifecycle-key"}
SOURCE = "# notebook-to-api: background\ndef work(x: int) -> int:\n    return x\n"


@pytest.fixture
def app(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "lifecycle_app"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "lifecycle-key")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("lifecycle_app.app")
    now = time.time()
    with TestClient(module.app) as client:
        module.TASKS.clear()
        for task_id, status in (("p", "processing"), ("c", "completed"), ("f", "failed"), ("x1", "cancelled")):
            module.TASKS[task_id] = {"status": status, "created_at": now, "endpoint": "/work"}
        yield module, client


def test_filtering_by_cancelled_succeeds_and_lists_cancelled_tasks(app):
    module, client = app
    # Cancel one through the real endpoint so the status value is the one the app writes.
    module.TASKS["q"] = {"status": "processing", "created_at": time.time()}
    assert client.post("/tasks/q/cancel", headers=HEADERS).json()["status"] == "cancelled"

    body = client.get("/tasks?status=cancelled", headers=HEADERS).json()
    assert set(body["tasks"]) == {"x1", "q"}
    assert body["matching_tasks"] == 2
    unfiltered = client.get("/tasks", headers=HEADERS).json()
    assert {"x1", "q"} <= set(unfiltered["tasks"])

    bad = client.get("/tasks?status=nonsense", headers=HEADERS)
    assert bad.status_code == 400 and "cancelled" in bad.json()["detail"]


def test_summary_counts_and_metrics_include_cancelled_without_changing_other_fields(app):
    _, client = app
    summary = client.get("/tasks", headers=HEADERS).json()
    assert summary["cancelled_tasks"] == 1
    assert (summary["processing_tasks"], summary["completed_tasks"], summary["failed_tasks"]) == (1, 1, 1)
    assert summary["active_tasks"] == 4

    metrics = client.get("/metrics", headers=HEADERS).json()
    assert metrics["cancelled"] == 1
    assert (metrics["total_tasks"], metrics["processing"], metrics["completed"], metrics["failed"]) == (4, 1, 1, 1)

    prom = client.get("/metrics/prometheus", headers=HEADERS).text
    assert "notebook_api_tasks_cancelled 1\n" in prom and "notebook_api_tasks_failed 1\n" in prom


def test_cleanup_removes_cancelled_and_reports_it_additively(app):
    module, client = app
    result = client.post("/tasks/cleanup", headers=HEADERS).json()
    assert result == {"completed_deleted": 1, "failed_deleted": 1, "cancelled_deleted": 1, "remaining_tasks": 1}
    assert set(module.TASKS) == {"p"}  # only the still-processing task is left


def test_delete_failed_keeps_its_meaning_and_completed_is_unaffected(app):
    module, client = app
    assert client.delete("/tasks/failed", headers=HEADERS).json()["deleted"] == 1
    assert set(module.TASKS) == {"p", "c", "x1"}  # cancelled is not "failed"
    assert client.delete("/tasks/completed", headers=HEADERS).json()["deleted"] == 1
    assert set(module.TASKS) == {"p", "x1"}
    # Explicit per-task deletion still works for a cancelled task.
    assert client.delete("/tasks/x1", headers=HEADERS).status_code == 200
    assert set(module.TASKS) == {"p"}


def test_python_sdk_documents_the_cancelled_status(tmp_path):
    import json
    schema = tmp_path / "openapi.json"
    schema.write_text(json.dumps({"paths": {"/work": {"post": {"operationId": "work"}}}}), encoding="utf-8")
    out = tmp_path / "client.py"
    generate_python_sdk(str(schema), str(out))
    source = out.read_text(encoding="utf-8")
    assert '"cancelled"' in source and "cancelled_tasks" in source
    assert "cancelled_deleted" in source
    compile(source, str(out), "exec")  # still valid Python
