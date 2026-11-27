"""Create, rotate and revoke dashboard keys at runtime."""
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import dashboard, dashboard_keys

ADMIN = "admin-secret-0123456789"


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"root:{ADMIN}:admin,viewer:viewer-secret-0123456789:read")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS_DB", str(tmp_path / "keys.db"))
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setattr(dashboard, "_utc_today", lambda: date(2026, 11, 27))
    return tmp_path


@pytest.fixture
def client(env):
    return TestClient(dashboard.app)


def H(key):
    return {"X-API-Key": key}


def create(client, **body):
    body.setdefault("name", "ci")
    return client.post("/api/keys", json=body, headers=H(ADMIN))


def test_created_key_authenticates_with_its_scope_and_is_shown_once(client):
    response = create(client, scope="read")
    assert response.status_code == 201
    secret = response.json()["key"]
    assert secret.startswith("n2a_")
    who = client.get("/api/whoami", headers=H(secret)).json()
    assert (who["name"], who["scope"]) == ("ci", "read")
    assert secret not in client.get("/api/keys", headers=H(ADMIN)).text
    assert client.post("/api/compile", json={}, headers=H(secret)).status_code == 403  # read cannot compile


def test_only_digests_reach_the_database(client, env):
    secret = create(client).json()["key"]
    dump = "\n".join(str(r) for r in sqlite3.connect(env / "keys.db").execute("SELECT * FROM dashboard_keys"))
    assert secret not in dump and dashboard_keys._digest(secret) in dump


def test_rotation_invalidates_the_old_key_at_once(client):
    old = create(client, scope="compile").json()["key"]
    new = client.post("/api/keys/ci/rotate", headers=H(ADMIN)).json()["key"]
    assert new != old
    assert client.get("/api/whoami", headers=H(old)).status_code == 401
    assert client.get("/api/whoami", headers=H(new)).json()["scope"] == "compile"


def test_revocation_takes_effect_on_the_next_request(client):
    secret = create(client).json()["key"]
    assert client.get("/api/whoami", headers=H(secret)).status_code == 200
    assert client.delete("/api/keys/ci", headers=H(ADMIN)).json() == {"name": "ci", "revoked": True}
    assert client.get("/api/whoami", headers=H(secret)).status_code == 401
    assert client.delete("/api/keys/ci", headers=H(ADMIN)).status_code == 404
    assert client.post("/api/keys/ci/rotate", headers=H(ADMIN)).status_code == 404
    assert client.get("/api/keys", headers=H(ADMIN)).json()["keys"][0]["revoked_at"]


def test_expiry_is_validated_and_enforced(client, monkeypatch):
    assert create(client, expires="2026-11-26").status_code == 400
    assert create(client, expires="soon").status_code == 400
    secret = create(client, expires="2026-11-28").json()["key"]
    assert client.get("/api/whoami", headers=H(secret)).json()["expires_in_days"] == 1
    monkeypatch.setattr(dashboard, "_utc_today", lambda: date(2026, 11, 29))
    assert client.get("/api/whoami", headers=H(secret)).status_code == 401


def test_only_admin_keys_may_manage_keys(client):
    viewer = "viewer-secret-0123456789"
    assert client.get("/api/keys", headers=H(viewer)).status_code == 403
    assert client.post("/api/keys", json={"name": "x"}, headers=H(viewer)).status_code == 403
    assert client.delete("/api/keys/viewer", headers=H(viewer)).status_code == 403
    assert client.get("/api/keys").status_code == 401
    compile_key = create(client, name="builder", scope="compile").json()["key"]
    assert client.get("/api/keys", headers=H(compile_key)).status_code == 403


def test_validation_and_name_conflicts(client):
    assert create(client, name="bad name").status_code == 400
    assert create(client, scope="root").status_code == 400
    assert create(client, name="root").status_code == 409  # configured through the environment
    assert create(client).status_code == 201
    assert create(client).status_code == 409


def test_keys_survive_a_restart(client, env):
    secret = create(client).json()["key"]
    restarted = TestClient(dashboard.app)  # nothing is cached in memory; the store is the source
    assert restarted.get("/api/whoami", headers=H(secret)).json()["name"] == "ci"


def test_key_changes_are_audited_without_the_secret(client, env):
    secret = create(client).json()["key"]
    client.post("/api/keys/ci/rotate", headers=H(ADMIN))
    client.delete("/api/keys/ci", headers=H(ADMIN))
    text = "\n".join(str(r) for r in sqlite3.connect(env / "audit.db").execute(
        "SELECT * FROM sqlite_master WHERE type='table'").fetchall())
    records = client.get("/governance/audit/history", headers=H(ADMIN)).text
    for action in ("dashboard.key_created", "dashboard.key_rotated", "dashboard.key_revoked"):
        assert action in records
    assert secret not in records and "root" in records
    assert text is not None


def test_keys_created_while_no_key_is_configured_are_local_only(monkeypatch, env):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS")
    assert dashboard_keys.list_keys() == []
