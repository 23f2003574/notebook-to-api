"""Generated dashboard deployments hide service storage by default and the health check tells the truth."""
import pytest
from fastapi.testclient import TestClient

from backend.generator import dashboard_deployment
from backend.runtime import isolated_execution as iso




def test_every_generated_file_enables_and_requires_hiding():
    src = open(dashboard_deployment.__file__).read()
    assert src.count("NOTEBOOK_API_ISOLATE_HIDE_STORAGE") >= 3
    assert src.count("NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN") >= 3


def test_status_disabled_enforced_and_unavailable(monkeypatch):
    assert iso.isolation_status({})["state"] == "disabled"
    env = {"NOTEBOOK_API_ISOLATE_HIDE_STORAGE": "1"}
    monkeypatch.setattr(iso, "landlock_available", lambda: True)
    assert iso.isolation_status(env)["state"] == "enforced"
    monkeypatch.setattr(iso, "landlock_available", lambda: False)
    assert iso.isolation_status(env)["state"] == "unavailable"


def test_startup_fails_closed_only_when_required(monkeypatch):
    monkeypatch.setattr(iso, "landlock_available", lambda: False)
    env = {"NOTEBOOK_API_ISOLATE_HIDE_STORAGE": "1"}
    assert iso.check_isolation_for_startup(env)["state"] == "unavailable"  # opt-out: running unprotected, visibly
    with pytest.raises(iso.IsolatedExecutionError, match="seccomp"):
        iso.check_isolation_for_startup({**env, "NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN": "1"})
    monkeypatch.setattr(iso, "landlock_available", lambda: True)
    assert iso.check_isolation_for_startup({**env, "NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN": "1"})["state"] == "enforced"


def test_health_reports_the_effective_state(monkeypatch):
    from backend.dashboard import app

    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_HIDE_STORAGE", "1")
    monkeypatch.setattr(iso, "landlock_available", lambda: False)
    body = TestClient(app).get("/api/health").json()
    assert body["isolation"]["state"] == "unavailable" and body["isolation"]["landlock_available"] is False
    monkeypatch.delenv("NOTEBOOK_API_ISOLATE_HIDE_STORAGE")
    assert TestClient(app).get("/api/health").json()["isolation"]["state"] == "disabled"
