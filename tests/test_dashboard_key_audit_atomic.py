"""A dashboard key change and its audit record succeed or fail together."""
import pytest
from fastapi.testclient import TestClient

from backend import dashboard, dashboard_keys
from backend.observability import deployment_governance_api as gov
from backend.observability.deployment_governance_audit import reset_audit_service
from tests.test_dashboard_key_management import ADMIN, H, create, env  # noqa: F401 - the fixture


@pytest.fixture
def client(env):  # noqa: F811
    reset_audit_service()
    yield TestClient(dashboard.app)
    reset_audit_service()


REAL = gov._build_audit_service


def _restore_audit(monkeypatch):
    monkeypatch.setattr(gov, "_build_audit_service", REAL)


def _break_audit(monkeypatch):
    def fail(*a, **k):
        raise OSError("audit database is unavailable")
    monkeypatch.setattr(gov, "_build_audit_service", fail)


def _records(client):
    return client.get("/governance/audit?limit=100", headers=H(ADMIN)).json()


def test_success_writes_the_key_and_its_record_and_the_chain_verifies(client):
    assert create(client, name="ci", scope="read").status_code == 201
    assert client.post("/api/keys/ci/rotate", headers=H(ADMIN)).status_code == 200
    assert client.delete("/api/keys/ci", headers=H(ADMIN)).status_code == 200
    actions = [r["action"] for r in _records(client) if r["resource"] == "dashboard-key:ci"]
    assert {"dashboard.key_created", "dashboard.key_rotated", "dashboard.key_revoked"} <= set(actions)
    assert gov._build_audit_service().verify_chain().valid is True


def test_create_is_rolled_back_when_the_audit_record_cannot_be_written(client, monkeypatch):
    _break_audit(monkeypatch)
    response = create(client, name="ci")
    assert response.status_code == 503 and "audit" in response.json()["detail"]
    assert [k["name"] for k in dashboard_keys.list_keys()] == []
    assert "key" not in response.text


def test_rotate_and_revoke_are_rolled_back_too(client, monkeypatch):
    secret = create(client, name="ci", scope="admin").json()["key"]
    _break_audit(monkeypatch)
    assert client.post("/api/keys/ci/rotate", headers=H(ADMIN)).status_code == 503
    assert client.delete("/api/keys/ci", headers=H(ADMIN)).status_code == 503
    _restore_audit(monkeypatch)
    assert dashboard_keys.list_keys()[0]["revoked_at"] is None and dashboard_keys.list_keys()[0]["rotated_at"] is None
    assert client.get("/api/keys", headers=H(secret)).status_code == 200  # the original secret still works


def test_conflicts_and_missing_keys_keep_their_own_status_codes(client):
    assert create(client, name="ci").status_code == 201
    assert create(client, name="ci").status_code == 409
    assert client.post("/api/keys/nope/rotate", headers=H(ADMIN)).status_code == 404
    assert client.delete("/api/keys/nope", headers=H(ADMIN)).status_code == 404
    assert [r["action"] for r in _records(client)].count("dashboard.key_created") == 1


def test_a_failed_audit_write_leaves_the_chain_intact(client, monkeypatch):
    create(client, name="a")
    _break_audit(monkeypatch)
    create(client, name="b")
    _restore_audit(monkeypatch)
    create(client, name="c")
    assert {k["name"] for k in dashboard_keys.list_keys()} == {"a", "c"}
    assert gov._build_audit_service().verify_chain().valid is True
