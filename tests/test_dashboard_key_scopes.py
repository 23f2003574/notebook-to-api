"""Scoped dashboard keys: read / compile / admin, backward compatible with unscoped keys."""
import hashlib

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import auth_metrics, dashboard
from backend.observability.deployment_governance_audit import reset_audit_service
from backend.routes import upload

RD, CP, AD, LEG = "reader-key-0123456789", "compile-key-0123456789", "admin-key-0123456789", "legacy-key-0123456789"
NB = nbformat.writes(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("def f(a: int) -> int:\n    return a\n")]))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS_FILE", "NOTEBOOK2API_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"viewer:{RD}:read,ci:{CP}:compile,boss:{AD}:admin,old:{LEG}")
    reset_audit_service()
    yield
    reset_audit_service()


def _as(key):
    c = TestClient(dashboard.app)
    c.headers.update({"X-API-Key": key})
    return c


def _upload(c, name="a.ipynb"):
    return c.post("/api/upload", files={"file": (name, NB.encode())})


def test_read_scope_can_read_but_not_write():
    c = _as(RD)
    assert c.get("/api/health").status_code == 200 and c.get("/api/notebooks").status_code == 200
    assert _upload(c).status_code == 403
    assert c.post("/api/compile", json={"notebook_path": "a.ipynb"}).status_code == 403
    assert c.delete("/api/notebooks/a.ipynb").status_code == 403
    assert c.post("/api/deploy", json={}).status_code == 403
    assert c.post("/api/validate", json={"notebook_path": "a.ipynb"}).status_code == 403


def test_compile_scope_can_compile_but_not_administer():
    c = _as(CP)
    assert _upload(c).status_code == 200
    assert c.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code == 200
    assert c.post("/api/validate", json={"notebook_path": "a.ipynb"}).status_code != 403
    assert c.post("/api/requirements-preview", json={"notebook_path": "a.ipynb"}).status_code != 403
    assert c.delete("/api/notebooks/a.ipynb").status_code == 403
    assert c.patch("/api/notebooks/a.ipynb", json={"new_filename": "b.ipynb"}).status_code == 403
    assert c.post("/api/deploy", json={"notebook": "a.ipynb"}).status_code == 403
    assert c.delete("/api/compile/history").status_code == 403


def test_admin_and_unscoped_keys_keep_every_privilege():
    for key in (AD, LEG):
        c = _as(key)
        assert _upload(c, f"{key[:3]}.ipynb").status_code == 200
        assert c.delete(f"/api/notebooks/{key[:3]}.ipynb").status_code == 200


def test_legacy_single_key_is_admin(monkeypatch):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", LEG)
    assert _upload(_as(LEG)).status_code == 200


def test_invalid_scope_is_rejected(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"x:{RD}:superuser")
    with pytest.raises(ValueError, match="unknown scope") as err:
        dashboard.dashboard_api_keys()
    assert RD not in str(err.value)


def test_digest_keys_and_plain_colons_parse(monkeypatch):
    digest = hashlib.sha256(b"secret-secret-secret").hexdigest()
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"a:sha256:{digest}:read,b:sha256:{digest},c:has:colon:1234")
    entries, scopes = dashboard.dashboard_api_keys_and_scopes()
    assert scopes == {"a": "read", "b": "admin", "c": "admin"} and len(entries) == 3


def test_matrix_helper():
    assert dashboard.required_scope("GET", "/anything") == "read"
    assert dashboard.required_scope("POST", "/api/compile") == "compile"
    assert dashboard.required_scope("POST", "/api/curl-preview") == "compile"
    for method, path in (("DELETE", "/api/notebooks/x"), ("POST", "/api/deploy"), ("PUT", "/api/notebooks/x/tags"),
                         ("POST", "/some/new/route")):
        assert dashboard.required_scope(method, path) == "admin"


def test_denials_are_audited_and_counted_without_key_values():
    before = auth_metrics.totals()["denied"]
    c = _as(RD)
    assert _upload(c).status_code == 403
    assert auth_metrics.totals()["denied"] == before + 1
    assert "auth_denied_total" in auth_metrics.prometheus_text()
    records = _as(AD).get("/governance/audit/history?limit=20").json()["records"]
    denied = [r for r in records if r["action"] == "dashboard.authorization_denied"]
    assert denied and denied[0]["actor"] == "viewer" and denied[0]["outcome"] == "failure"
    assert RD not in str(records)
