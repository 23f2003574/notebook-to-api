import importlib

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

SOURCE = 'def ping() -> dict:\n    """Say pong."""\n    return {"pong": True}\n'


def _app(tmp_path, monkeypatch, name):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / name))
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module(f"{name}.app")


def test_health_is_liveness_and_ready_reports_ready(tmp_path, monkeypatch):
    client = TestClient(_app(tmp_path, monkeypatch, "ready_ok").app)

    assert client.get("/health").json() == {"status": "healthy"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert set(ready.json()) == {"status", "tasks_registered", "isolation"} and ready.json()["status"] == "ready"
    assert client.head("/ready").status_code == 200


def test_ready_turns_503_while_shutting_down_but_health_stays_up(tmp_path, monkeypatch):
    module = _app(tmp_path, monkeypatch, "ready_drain")
    with TestClient(module.app) as client:
        assert client.get("/ready").status_code == 200
        module._LIFECYCLE["state"] = "shutting_down"
        draining = client.get("/ready")
        assert draining.status_code == 503
        assert draining.json() == {"status": "not_ready", "reason": "shutting_down"}
        assert client.head("/ready").status_code == 503
        assert client.get("/health").status_code == 200
        module._LIFECYCLE["state"] = "ready"


def test_lifespan_exit_marks_the_app_not_ready(tmp_path, monkeypatch):
    module = _app(tmp_path, monkeypatch, "ready_exit")
    with TestClient(module.app):
        pass

    assert module._LIFECYCLE["state"] == "shutting_down"


def test_ready_response_leaks_no_paths_or_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_KEY", "ready-secret-key")
    module = _app(tmp_path, monkeypatch, "ready_leak")
    module._LIFECYCLE["state"] = "shutting_down"
    body = TestClient(module.app).get("/ready").text

    assert "ready-secret-key" not in body and str(tmp_path) not in body
