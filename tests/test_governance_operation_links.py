"""Compile and deploy results point at their governance audit record."""
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
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp_path / "uploads" / "link_nb.ipynb")
    return tmp_path


def test_a_successful_compile_carries_the_sequence_of_its_one_record(env):
    body = client.post("/api/compile", json={"notebook_path": "link_nb.ipynb", "per_notebook": True}).json()
    ref = body["governance_audit"]
    record = client.get(f"/governance/audit/{ref['sequence']}").json()
    assert record["record_hash"] == ref["record_hash"]
    assert record["action"] == "notebook.compile" and record["outcome"] == "success"
    assert record["metadata"]["notebook"] == "link_nb.ipynb"
    mine = client.get("/governance/audit", params={"resource": "notebook:link_nb.ipynb"}).json()
    assert [r["sequence"] for r in mine] == [ref["sequence"]]  # linking added no extra record


def test_a_failed_operation_exposes_its_record_through_a_header(env):
    resp = client.post("/api/compile", json={"notebook_path": "missing_link.ipynb"})
    assert resp.status_code >= 400
    sequence = int(resp.headers["X-Governance-Audit-Sequence"])
    record = client.get(f"/governance/audit/{sequence}").json()
    assert record["outcome"] == "failure" and record["resource"] == "notebook:missing_link.ipynb"
    failed_deploy = client.post("/api/deploy", json={"notebook": "none_link.ipynb", "dry_run": True})
    assert int(failed_deploy.headers["X-Governance-Audit-Sequence"]) > sequence


def test_a_deploy_success_links_too(env):
    client.post("/api/compile", json={"notebook_path": "link_nb.ipynb", "per_notebook": True})
    done = client.post("/api/deploy", json={"notebook": "link_nb.ipynb", "dry_run": True}).json()
    assert client.get(f"/governance/audit/{done['governance_audit']['sequence']}").json()["action"] == "notebook.deploy"


def test_an_unknown_sequence_is_a_404_and_audit_failure_keeps_the_result(env, monkeypatch):
    assert client.get("/governance/audit/99999999").status_code == 404
    import backend.observability.deployment_governance_api as gov

    monkeypatch.setattr(gov, "_build_audit_service", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    body = client.post("/api/compile", json={"notebook_path": "link_nb.ipynb", "per_notebook": True}).json()
    assert "governance_audit" not in body and "governance_audit_error" in body
    resp = client.post("/api/compile", json={"notebook_path": "missing_link2.ipynb"})
    assert resp.status_code >= 400 and "X-Governance-Audit-Sequence" not in resp.headers
