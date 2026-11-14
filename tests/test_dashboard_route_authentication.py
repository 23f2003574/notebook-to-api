"""Every dashboard operation requires the dashboard key; with no key, only local peers get in."""
import pytest
from fastapi.testclient import TestClient

import backend.app  # noqa: F401  (registers the v2 security routers on the same app)
from backend.dashboard import app

KEY = "route-auth-key-4821"
METHODS = ("get", "post", "put", "patch", "delete")
ROUTES = sorted(
    (method.upper(), path)
    for path, ops in app.openapi()["paths"].items()
    for method in ops if method in METHODS
)


def _url(path):
    return path.replace("{", "x").replace("}", "")


def _client(peer=None):
    return TestClient(app, raise_server_exceptions=False, client=peer) if peer else TestClient(app, raise_server_exceptions=False)


def test_route_inventory_is_large_and_includes_the_sensitive_groups():
    paths = {path for _, path in ROUTES}
    assert len(ROUTES) > 300
    for needle in ("/api/upload", "/api/compile", "/secrets", "/api-keys"):
        assert any(needle in p for p in paths), needle


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_every_route_rejects_missing_and_wrong_keys(monkeypatch, headers):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    client = _client()
    leaked = [(m, p) for m, p in ROUTES
              if (r := client.request(m, _url(p), headers=headers, json={})).status_code != 401 or KEY in r.text]
    assert leaked == []


def test_correct_key_reaches_the_routes(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    client = _client()
    headers = {"X-API-Key": KEY}
    assert client.get("/api/health", headers=headers).json()["status"] == "healthy"
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/api-keys", headers=headers).status_code != 401
    assert client.post("/api/compile", json={}, headers=headers).status_code != 401


def test_failures_do_not_echo_the_credential_or_secret_names(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    r = _client().get("/api/secrets/db-password", headers={"X-API-Key": "guess-123"})
    assert r.status_code == 401 and r.json() == {"detail": "Invalid API key"}
    assert "guess-123" not in r.text and KEY not in r.text and "db-password" not in r.text


def test_empty_key_does_not_open_the_dashboard_to_remote_peers(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "")
    remote = _client(("203.0.113.9", 5000))
    for method, path in (("GET", "/api/secrets/x"), ("POST", "/api/upload"), ("DELETE", "/api/notebooks/x.ipynb"),
                         ("POST", "/api/compile"), ("GET", "/api/health")):
        r = remote.request(method, path, json={})
        assert r.status_code == 401 and "NOTEBOOK_API_DASHBOARD_KEY" in r.json()["detail"], (method, path)


def test_without_a_key_local_peers_keep_the_development_experience(monkeypatch):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY", raising=False)
    assert _client(("127.0.0.1", 5000)).get("/api/health").status_code == 200
    assert _client(("::1", 5000)).get("/api/health").status_code == 200
    assert _client().get("/api/health").status_code == 200


def test_remote_peer_with_the_key_is_allowed(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    r = _client(("203.0.113.9", 5000)).get("/api/health", headers={"X-API-Key": KEY})
    assert r.status_code == 200
