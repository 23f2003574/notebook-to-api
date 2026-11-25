"""The dashboard's preview routes (what the UI shows) include the hardening the compiler writes."""
from fastapi.testclient import TestClient

from backend import dashboard


def _client(monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    return TestClient(dashboard.app)


def test_previews_show_non_root_read_only_dropped_capabilities(monkeypatch):
    c = _client(monkeypatch)
    assert "USER appuser" in c.get("/api/dockerfile-preview").json()["dockerfile"]
    compose = c.get("/api/docker-compose-preview").json()["docker_compose"]
    assert 'cap_drop: ["ALL"]' in compose and "read_only:" in compose and "no-new-privileges" in compose
    k8s = c.get("/api/k8s-preview").json()["kubernetes_manifest"]
    assert "readOnlyRootFilesystem: true" in k8s and "runAsNonRoot: true" in k8s and "seccompProfile" in k8s
    assert "NOTEBOOK_API_KEY" in c.get("/api/env-example-preview").json()["env_example"]
