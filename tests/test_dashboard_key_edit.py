"""Edit a managed key's scope and expiry, and see when it was last used."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import dashboard, dashboard_keys
from backend.observability.deployment_governance_audit import reset_audit_service
from tests.test_dashboard_key_management import ADMIN, H, create, env  # noqa: F401


@pytest.fixture
def client(env):  # noqa: F811
    reset_audit_service()
    dashboard_keys._LAST_TOUCH.clear()
    yield TestClient(dashboard.app)
    reset_audit_service()


def patch(client, name, key=ADMIN, **body):
    return client.patch(f"/api/keys/{name}", json=body, headers=H(key))


def test_scope_change_is_enforced_on_the_next_request(client):
    secret = create(client, name="ci", scope="read").json()["key"]
    assert client.get("/api/keys", headers=H(secret)).status_code == 403
    assert patch(client, "ci", scope="admin").json() == {"name": "ci", "scope": "admin"}
    assert client.get("/api/keys", headers=H(secret)).status_code == 200
    patch(client, "ci", scope="read")
    assert client.get("/api/keys", headers=H(secret)).status_code == 403


def test_expiry_can_be_set_extended_and_removed(client):
    secret = create(client, name="ci", scope="admin", expires="2026-12-01").json()["key"]
    assert patch(client, "ci", expires="2026-11-27").status_code == 200  # today is still valid
    assert patch(client, "ci", expires="2027-01-01").json()["expires"] == "2027-01-01"
    assert patch(client, "ci", expires=None).json()["expires"] is None
    assert client.get("/api/keys", headers=H(secret)).status_code == 200
    assert next(k for k in dashboard_keys.list_keys() if k["name"] == "ci")["expires"] is None


def test_invalid_requests_missing_keys_and_unauthorized_callers(client):
    secret = create(client, name="ci", scope="compile").json()["key"]
    assert patch(client, "ci", scope="root").status_code == 400
    assert patch(client, "ci", expires="2026-01-01").status_code == 400  # in the past
    assert patch(client, "ci", expires="soon").status_code == 400
    assert patch(client, "ci").status_code == 400
    assert patch(client, "nope", scope="read").status_code == 404
    assert patch(client, "root", scope="read").status_code == 404  # environment keys are not editable
    assert patch(client, "ci", key=secret, scope="admin").status_code == 403  # a compile key cannot promote itself
    assert patch(client, "ci", key="wrong-key-0123456789", scope="admin").status_code == 401
    client.delete("/api/keys/ci", headers=H(ADMIN))
    assert patch(client, "ci", scope="read").status_code == 404  # revoked stays revoked


def test_update_is_audited_without_any_secret(client):
    secret = create(client, name="ci").json()["key"]
    patch(client, "ci", scope="compile")
    records = client.get("/governance/audit?limit=50", headers=H(ADMIN)).json()
    updated = [r for r in records if r["action"] == "dashboard.key_updated"]
    assert updated and updated[0]["resource"] == "dashboard-key:ci"
    assert secret not in str(records)


def test_last_use_is_recorded_throttled_and_survives_restart(client, env):
    secret = create(client, name="ci", scope="read").json()["key"]
    assert [k for k in dashboard_keys.list_keys() if k["name"] == "ci"][0]["last_used_at"] is None
    client.get("/api/health", headers=H(secret))
    first = [k for k in dashboard_keys.list_keys() if k["name"] == "ci"][0]["last_used_at"]
    assert first is not None
    with sqlite3.connect(env / "keys.db") as db:
        db.execute("UPDATE dashboard_keys SET last_used_at='old' WHERE name='ci'")
    client.get("/api/health", headers=H(secret))  # inside the interval: no write
    assert [k for k in dashboard_keys.list_keys() if k["name"] == "ci"][0]["last_used_at"] == "old"
    dashboard_keys._LAST_TOUCH.clear()  # a restarted worker
    client.get("/api/health", headers=H(secret))
    assert [k for k in dashboard_keys.list_keys() if k["name"] == "ci"][0]["last_used_at"] != "old"


def test_a_store_made_before_the_column_existed_is_upgraded(client, env):
    with sqlite3.connect(env / "keys.db") as db:
        db.execute("CREATE TABLE dashboard_keys (name TEXT PRIMARY KEY, digest TEXT NOT NULL, scope TEXT NOT NULL,"
                   " expires TEXT, created_at TEXT NOT NULL, rotated_at TEXT, revoked_at TEXT)")
        db.execute("INSERT INTO dashboard_keys VALUES ('old','00','read',NULL,'x',NULL,NULL)")
    dashboard_keys._READY.clear()
    assert dashboard_keys.list_keys()[0]["last_used_at"] is None
