"""Dashboard keys may carry `@YYYY-MM-DD`: valid through that UTC day, refused after."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import dashboard

KEY = "alice-secret-0123456789"


@pytest.fixture
def clock(monkeypatch):
    today = {"value": date(2026, 11, 25)}
    monkeypatch.setattr(dashboard, "_utc_today", lambda: today["value"])
    return today


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY", raising=False)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS_FILE", raising=False)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "0")

    def make(keys):
        monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", keys)
        return TestClient(dashboard.app)
    return make


def test_key_valid_through_its_expiry_day(client, clock):
    c = client(f"alice:{KEY}:read@2026-11-25")
    assert c.get("/api/whoami", headers={"X-API-Key": KEY}).status_code == 200


def test_key_refused_the_day_after(client, clock):
    c = client(f"alice:{KEY}:read@2026-11-24")
    response = c.get("/api/whoami", headers={"X-API-Key": KEY})
    assert response.status_code == 401 and response.json() == {"detail": "Invalid API key"}


def test_whoami_reports_expiry_without_the_key(client, clock):
    data = client(f"alice:{KEY}:read@2026-12-01").get("/api/whoami", headers={"X-API-Key": KEY}).json()
    assert data["expires"] == "2026-12-01" and data["expires_in_days"] == 6 and KEY not in str(data)


def test_keys_without_expiry_and_legacy_format_still_work(client, clock):
    c = client(f"alice:{KEY}, bob:{KEY}b:compile")
    data = c.get("/api/whoami", headers={"X-API-Key": KEY + "b"}).json()
    assert data["scope"] == "compile" and data["expires"] is None
    assert c.get("/api/whoami", headers={"X-API-Key": KEY}).json()["scope"] == "admin"


def test_expiry_works_with_a_digest_key(client, clock):
    digest = dashboard._key_digest(KEY).hex()
    c = client(f"alice:sha256:{digest}:read@2026-11-20")
    assert c.get("/api/whoami", headers={"X-API-Key": KEY}).status_code == 401


@pytest.mark.parametrize("suffix", ["@2026-13-01", "@2026-02-30"])
def test_invalid_date_fails_startup_validation(monkeypatch, suffix):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"alice:{KEY}{suffix}")
    with pytest.raises(ValueError, match="invalid expiry date"):
        dashboard.validate_dashboard_auth_config("127.0.0.1")


def test_near_expiry_and_expired_keys_warn_at_startup(monkeypatch, clock, caplog):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"soon:{KEY}@2026-11-28,gone:{KEY}x@2026-11-01,far:{KEY}y@2027-06-01")
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        dashboard.validate_dashboard_auth_config("127.0.0.1")
    text = caplog.text
    assert "'soon' expires in 3 day(s)" in text and "'gone' expired on 2026-11-01" in text and "far" not in text
    assert KEY not in text
