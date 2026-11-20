"""Compile and deploy outcomes land in the existing governance audit chain."""
import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _notebook(env, name, src="def f(x: int) -> int:\n    return x\n"):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(src))
    nbformat.write(nb, env / "uploads" / name)


def _records(resource):
    return [r for r in client.get("/governance/audit", params={"resource": resource, "limit": 100}).json()]


def test_successful_compile_is_recorded_and_the_chain_verifies(env):
    _notebook(env, "ok_audit.ipynb")
    assert client.post("/api/compile", json={"notebook_path": "ok_audit.ipynb", "per_notebook": True}).status_code == 200
    records = _records("notebook:ok_audit.ipynb")
    assert len(records) == 1
    rec = records[0]
    assert rec["action"] == "notebook.compile" and rec["outcome"] == "success" and rec["actor"] == "dashboard"
    assert rec["metadata"]["notebook"] == "ok_audit.ipynb" and rec["metadata"]["per_notebook"] is True
    assert rec["occurred_at"]
    assert client.get("/governance/audit/verify").json()["valid"] is True


def test_failed_compile_records_an_accurate_failure(env):
    (env / "uploads" / "bad_audit.ipynb").write_text("{not a notebook")
    resp = client.post("/api/compile", json={"notebook_path": "bad_audit.ipynb", "per_notebook": True})
    assert resp.status_code >= 400
    records = _records("notebook:bad_audit.ipynb")
    assert [r["outcome"] for r in records] == ["failure"]
    assert records[0]["metadata"]["error"]
    assert client.get("/governance/audit/verify").json()["valid"] is True


def test_missing_notebook_compile_failure_is_recorded(env):
    assert client.post("/api/compile", json={"notebook_path": "ghost_audit.ipynb"}).status_code >= 400
    assert [r["outcome"] for r in _records("notebook:ghost_audit.ipynb")] == ["failure"]


def test_deploy_success_and_failure_are_recorded(env):
    failed = client.post("/api/deploy", json={"notebook": "nodeploy_audit.ipynb", "dry_run": True})
    assert failed.status_code >= 400
    assert [(r["action"], r["outcome"]) for r in _records("notebook:nodeploy_audit.ipynb")] == [("notebook.deploy", "failure")]

    _notebook(env, "dep_audit.ipynb")
    assert client.post("/api/compile", json={"notebook_path": "dep_audit.ipynb", "per_notebook": True}).status_code == 200
    done = client.post("/api/deploy", json={"notebook": "dep_audit.ipynb", "dry_run": True})
    assert done.status_code == 200, done.text
    actions = sorted((r["action"], r["outcome"]) for r in _records("notebook:dep_audit.ipynb"))
    assert actions == [("notebook.compile", "success"), ("notebook.deploy", "success")]
    assert client.get("/governance/audit/verify").json()["valid"] is True


def test_each_request_records_exactly_once(env):
    _notebook(env, "once_audit.ipynb")
    for _ in range(2):
        client.post("/api/compile", json={"notebook_path": "once_audit.ipynb", "per_notebook": True})
    records = _records("notebook:once_audit.ipynb")
    assert len(records) == 2 and len({r["sequence"] for r in records}) == 2  # two requests, two distinct events


def test_audit_failure_does_not_change_the_operation_outcome_and_is_surfaced(env, monkeypatch, caplog):
    import backend.observability.deployment_governance_api as gov

    def boom():
        raise RuntimeError("audit store down")

    monkeypatch.setattr(gov, "_build_audit_service", boom)
    _notebook(env, "down_audit.ipynb")
    ok = client.post("/api/compile", json={"notebook_path": "down_audit.ipynb", "per_notebook": True})
    assert ok.status_code == 200 and "governance_audit_error" in ok.json()
    bad = client.post("/api/compile", json={"notebook_path": "none_audit.ipynb"})
    assert bad.status_code >= 400  # the real failure is not masked
    assert "audit store down" in caplog.text
