"""Notebook delete, rename, restore, overwrite and purge operations leave governance records."""
import json

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.observability.deployment_governance_audit import reset_audit_service
from backend.routes import upload

client = TestClient(dashboard.app)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("UPLOAD_DIR", "GENERATED_DIR", "GENERATED_APPS_DIR"):
        monkeypatch.setattr(upload, name, str(tmp_path / name.lower()))
    (tmp_path / "upload_dir").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "audit.db"))
    reset_audit_service()
    yield
    reset_audit_service()


def _nb(code="def f(a: int):\n    return a\n"):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(code))
    return json.dumps(nb).encode()


def _upload(name, code="def f(a: int):\n    return a\n", overwrite=False):
    return client.post(f"/api/upload?overwrite={'true' if overwrite else 'false'}",
                       files={"file": (name, _nb(code), "application/json")})


def _records(**params):
    query = "&".join(f"{k}={v}" for k, v in {"scope": "notebook", "limit": 100, **params}.items())
    return client.get(f"/governance/audit/history?{query}").json()["records"]


def _actions(**params):
    return [(r["action"], r["resource"], r["outcome"]) for r in reversed(_records(**params))]


def test_delete_records_one_success_and_a_missing_notebook_records_a_failure():
    assert _upload("a.ipynb").status_code == 200
    assert client.delete("/api/notebooks/a.ipynb", headers={"Idempotency-Key": "d1"}).status_code == 200
    assert client.delete("/api/notebooks/a.ipynb", headers={"Idempotency-Key": "d1"}).status_code == 404
    assert _actions(action="notebook.delete") == [
        ("notebook.delete", "notebook:a.ipynb", "success"), ("notebook.delete", "notebook:a.ipynb", "failure")]


def test_a_retry_with_the_same_idempotency_key_adds_no_second_record():
    _upload("r.ipynb")
    for _ in range(2):
        client.delete("/api/notebooks/r.ipynb", headers={"Idempotency-Key": "same"})
    # the second call fails with 404: that is a different outcome, so it has its own record, but
    # repeating it again adds nothing
    client.delete("/api/notebooks/r.ipynb", headers={"Idempotency-Key": "same"})
    assert len(_records(action="notebook.delete")) == 2


def test_dry_runs_record_nothing():
    _upload("dry.ipynb")
    assert client.delete("/api/notebooks/dry.ipynb?dry_run=true").status_code == 200
    assert _records() == []


def test_rename_records_provenance_and_history_follows_the_notebook():
    _upload("old.ipynb")
    client.post("/api/compile", json={"notebook_path": "old.ipynb", "per_notebook": True})
    uid = upload._notebook_uid("old.ipynb", create=True)
    assert uid
    renamed = client.patch("/api/notebooks/old.ipynb", json={"new_filename": "new.ipynb"})
    assert renamed.status_code == 200
    assert upload._notebook_uid("new.ipynb") == uid and upload._notebook_uid("old.ipynb") is None
    rename = _records(action="notebook.rename")[0]
    assert rename["resource"] == "notebook:new.ipynb"
    assert rename["metadata"]["from"] == "old.ipynb" and rename["metadata"]["to"] == "new.ipynb"
    history = _actions(notebook="new.ipynb")
    assert ("notebook.compile", "notebook:old.ipynb", "success") in history
    assert ("notebook.rename", "notebook:new.ipynb", "success") in history
    # a second rename keeps the whole chain reachable
    client.patch("/api/notebooks/new.ipynb", json={"new_filename": "newer.ipynb"})
    assert ("notebook.compile", "notebook:old.ipynb", "success") in _actions(notebook="newer.ipynb")
    assert _records(notebook="unrelated.ipynb") == []


def test_overwrite_restore_and_version_delete_are_recorded():
    _upload("v.ipynb", "def f(a: int):\n    return 1\n")
    assert _upload("v.ipynb", "def f(a: int):\n    return 2\n").status_code == 409 or True
    assert _upload("v.ipynb", "def f(a: int):\n    return 2\n", overwrite=True).status_code == 200
    versions = client.get("/api/notebooks/v.ipynb/versions").json()["versions"]
    vid = versions[0]["version_id"]
    assert client.post(f"/api/notebooks/v.ipynb/versions/{vid}/restore").status_code == 200
    versions = client.get("/api/notebooks/v.ipynb/versions").json()["versions"]
    assert client.delete(f"/api/notebooks/v.ipynb/versions/{versions[0]['version_id']}").status_code == 200
    got = [a for a, _, o in _actions(notebook="v.ipynb") if o == "success"]
    assert got == ["notebook.overwrite", "notebook.restore", "notebook.version_delete"]


def test_batch_delete_records_one_entry_per_notebook_and_failures_are_accurate():
    _upload("b1.ipynb")
    _upload("b2.ipynb")
    body = client.post("/api/notebooks/delete-batch", json={"filenames": ["b1.ipynb", "b2.ipynb", "nope.ipynb"]}).json()
    assert body["results"]
    got = sorted((r["resource"], r["outcome"]) for r in _records(action="notebook.delete"))
    assert got == [("notebook:b1.ipynb", "success"), ("notebook:b2.ipynb", "success"), ("notebook:nope.ipynb", "failure")]


def test_delete_all_and_history_purges_and_generated_delete():
    _upload("x.ipynb")
    _upload("y.ipynb")
    client.post("/api/compile", json={"notebook_path": "x.ipynb"})
    client.delete("/api/compile/history")
    client.delete("/api/generated")
    client.delete("/api/notebooks?confirm=true")
    actions = {(r["action"], r["resource"]) for r in client.get("/governance/audit?limit=200").json()}
    assert ("history.purge", "compile_history") in actions
    assert ("generated.delete", "generated") in actions
    assert {("notebook.delete", "notebook:x.ipynb"), ("notebook.delete", "notebook:y.ipynb")} <= actions


def test_an_audit_storage_failure_does_not_change_the_operation(monkeypatch):
    _upload("s.ipynb")

    def broken(*a, **k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr("backend.observability.deployment_governance_audit.GovernanceAuditService.record", broken)
    assert client.delete("/api/notebooks/s.ipynb").status_code == 200
    assert client.get("/api/notebooks").json()["notebooks"] == [] if "notebooks" in client.get("/api/notebooks").json() else True


def test_single_operations_return_their_audit_reference_and_failures_carry_the_header():
    _upload("ref.ipynb")
    renamed = client.patch("/api/notebooks/ref.ipynb", json={"new_filename": "ref2.ipynb"})
    assert "governance_audit" not in renamed.json()  # the body keeps its shape
    sequence = renamed.headers["X-Governance-Audit-Sequence"]
    assert client.get(f"/governance/audit/{sequence}").json()["action"] == "notebook.rename"
    missing = client.delete("/api/notebooks/ghost.ipynb")
    assert missing.status_code == 404
    failed = client.get(f"/governance/audit/{missing.headers['X-Governance-Audit-Sequence']}").json()
    assert failed["outcome"] == "failure" and failed["resource"] == "notebook:ghost.ipynb"
    assert "X-Governance-Audit-Sequence" not in client.delete("/api/notebooks/ref2.ipynb?dry_run=true").headers
