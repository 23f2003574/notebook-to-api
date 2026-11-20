"""GET /governance/audit/verify tells a broken chain apart from an unreadable one."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.dashboard import app
from backend.observability import deployment_governance_audit as audit

client = TestClient(app)


@pytest.fixture
def durable(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "v.db"))
    audit.reset_audit_service()
    yield tmp_path / "v.db"
    audit.reset_audit_service()


def _write(n):
    svc = audit.get_audit_service()
    for i in range(n):
        svc.record(action="notebook.compile", actor="dashboard", resource=f"notebook:v{i}", outcome="success")


def test_an_intact_chain_verifies(durable):
    _write(3)
    body = client.get("/governance/audit/verify").json()
    assert body["valid"] is True and body["checked"] == 3


def test_a_tampered_record_is_located(durable):
    _write(3)
    with sqlite3.connect(durable) as conn:
        conn.execute("UPDATE audit_records SET outcome = 'failure' WHERE sequence = 2")
    body = client.get("/governance/audit/verify").json()
    assert body["valid"] is False and body["first_broken_sequence"] == 2 and "hash" in body["reason"]


def test_an_unreadable_store_is_unavailable_not_broken_and_leaks_no_path(durable):
    _write(1)
    audit.get_audit_service()._store.rows = lambda *a, **k: (_ for _ in ()).throw(
        __import__("backend.observability.deployment_governance_audit_store", fromlist=["x"]).GovernanceAuditStorageError("governance audit read failed (OperationalError)"))
    resp = client.get("/governance/audit/verify")
    assert resp.status_code == 503 and "unavailable" in resp.json()["detail"]
    assert str(durable.parent) not in resp.text


def test_an_empty_chain_is_valid_with_nothing_checked(durable):
    assert client.get("/governance/audit/verify").json()["checked"] == 0
