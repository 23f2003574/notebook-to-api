"""Named dashboard keys: several at once, rotation, digests, actor attribution, strength rules."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.observability.deployment_governance_audit import reset_audit_service
from backend.routes import upload

KEY_A, KEY_B = "alpha-key-0123456789", "bravo-key-9876543210"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE",
                 "NOTEBOOK2API_ENVIRONMENT", "NOTEBOOK_API_DASHBOARD_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    reset_audit_service()
    yield
    reset_audit_service()


def _client():
    return TestClient(dashboard.app)


def _get(client, key=None):
    return client.get("/api/health", headers={"X-API-Key": key} if key else {})


def test_two_keys_authenticate_concurrently_and_a_removed_key_is_rejected(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ci:{KEY_A},ops:{KEY_B}")
    client = _client()
    assert _get(client, KEY_A).status_code == 200 and _get(client, KEY_B).status_code == 200
    assert _get(client, "nope").status_code == 401 and _get(client).status_code == 401
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ops:{KEY_B}")  # rotation: ci is gone
    assert _get(client, KEY_A).status_code == 401 and _get(client, KEY_B).status_code == 200


def test_legacy_single_key_keeps_working_as_default(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY_A)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ops:{KEY_B}")
    client = _client()
    assert _get(client, KEY_A).status_code == 200 and _get(client, KEY_B).status_code == 200
    assert [name for name, _d, _l in dashboard.dashboard_api_keys()] == ["ops", "default"]


def test_digest_entries_and_key_files_work_without_storing_the_secret(tmp_path, monkeypatch):
    digest = hashlib.sha256(KEY_A.encode()).hexdigest()
    keys = tmp_path / "keys.txt"
    keys.write_text(f"# dashboard keys\nbuild: sha256:{digest}\n")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS_FILE", str(keys))
    client = _client()
    assert _get(client, KEY_A).status_code == 200 and _get(client, digest).status_code == 401


def test_actor_in_the_audit_record_is_the_key_name(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ci:{KEY_A},ops:{KEY_B}")
    client = _client()
    import json
    import nbformat
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(a: int):\n    return a\n"))
    for key in (KEY_A, KEY_B):
        headers = {"X-API-Key": key}
        client.post("/api/upload?overwrite=true", headers=headers,
                    files={"file": ("n.ipynb", json.dumps(nb).encode(), "application/json")})
        assert client.post("/api/compile", headers=headers, json={"notebook_path": "n.ipynb"}).status_code == 200
    client.delete("/api/notebooks/n.ipynb", headers={"X-API-Key": KEY_B})
    records = client.get("/governance/audit?limit=50", headers={"X-API-Key": KEY_A}).json()
    actors = {(r["action"], r["actor"]) for r in records}
    assert ("notebook.compile", "ci") in actors and ("notebook.compile", "ops") in actors
    assert ("notebook.delete", "ops") in actors


def test_failed_attempts_are_still_throttled_per_client(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ci:{KEY_A}")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "3")
    dashboard._AUTH_FAILURES.clear()
    client = _client()
    codes = [_get(client, "wrong").status_code for _ in range(6)]
    assert 429 in codes
    dashboard._AUTH_FAILURES.clear()


def test_keys_never_appear_in_errors(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ci:{KEY_A},ci:{KEY_B}")
    with pytest.raises(ValueError) as info:
        dashboard.dashboard_api_keys()
    assert KEY_A not in str(info.value) and KEY_B not in str(info.value)
    response = _get(_client(), KEY_A)  # misconfigured: fails closed with no detail
    assert response.status_code == 503 and KEY_A not in response.text
    for bad in ("justaname", "bad name:abc", "ok:sha256:1234", ":x"):
        monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", bad)
        with pytest.raises(ValueError):
            dashboard.dashboard_api_keys()


def test_production_network_deployments_reject_weak_keys(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", "ci:short,ops:" + KEY_B)
    env = {"NOTEBOOK2API_ENVIRONMENT": "production"}
    with pytest.raises(ValueError, match="ci") as info:
        dashboard.validate_dashboard_auth_config("0.0.0.0", env)
    assert "short" not in str(info.value).replace("shorter", "")
    dashboard.validate_dashboard_auth_config("127.0.0.1", env)  # loopback: allowed
    dashboard.validate_dashboard_auth_config("0.0.0.0", {})  # not production: allowed
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ops:{KEY_B},legacy:sha256:{'a' * 64}")
    dashboard.validate_dashboard_auth_config("0.0.0.0", env)


def test_named_keys_satisfy_the_non_loopback_exposure_check(monkeypatch):
    with pytest.raises(ValueError):
        dashboard.validate_dashboard_exposure("0.0.0.0", "")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ops:{KEY_B}")
    dashboard.validate_dashboard_exposure("0.0.0.0", "")
