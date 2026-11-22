from fastapi.testclient import TestClient

from backend.dashboard import app

client = TestClient(app)


def test_unconfigured_dashboard_stays_open(monkeypatch):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY", raising=False)

    assert client.get("/api/health").status_code == 200


def test_configured_dashboard_rejects_missing_and_wrong_keys(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "s3cret")

    for headers in ({}, {"X-API-Key": "wrong"}, {"X-API-Key": "s3cret-extra"}):
        response = client.get("/api/health", headers=headers)
        assert response.status_code == 401, headers
        assert response.json() == {"detail": "Invalid API key"}
        assert "s3cret" not in response.text


def test_configured_dashboard_accepts_the_right_key_and_cors_preflight(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "s3cret")

    assert client.get("/api/health", headers={"X-API-Key": "s3cret"}).status_code == 200

    preflight = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200
