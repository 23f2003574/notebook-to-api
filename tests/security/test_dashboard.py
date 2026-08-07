from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logs import AuditLogService
from backend.security.security_analytics import SecurityAnalyticsService
from backend.security.dashboard import SecurityDashboardAPI, router as dashboard_router

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def audit_log() -> AuditLogService:
    return AuditLogService()


@pytest.fixture
def analytics_service(audit_log: AuditLogService) -> SecurityAnalyticsService:
    return SecurityAnalyticsService(audit_log=audit_log)


@pytest.fixture
def dashboard(audit_log: AuditLogService, analytics_service: SecurityAnalyticsService) -> SecurityDashboardAPI:
    return SecurityDashboardAPI(audit_log=audit_log, analytics_service=analytics_service)


def _seed(audit_log: AuditLogService) -> None:
    audit_log.record("Authentication", "alice", "login", "attempt", outcome="success", timestamp=BASE_TIME)
    audit_log.record("Authentication", "eve", "login", "attempt", outcome="failure", timestamp=BASE_TIME)
    audit_log.record("Authorization", "alice", "notebooks", "check", outcome="success", timestamp=BASE_TIME)
    audit_log.record("Authorization", "bob", "notebooks", "check", outcome="denied", timestamp=BASE_TIME)
    audit_log.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)
    audit_log.record("Session", "alice", "session:2", "create", timestamp=BASE_TIME)
    audit_log.record("Session", "alice", "session:1", "terminate", timestamp=BASE_TIME)


def test_authentication_section(dashboard: SecurityDashboardAPI, audit_log: AuditLogService):
    _seed(audit_log)

    result = dashboard.authentication()

    assert result["total_events"] == 2
    assert result["successes"] == 1
    assert result["failures"] == 1
    assert result["success_rate"] == pytest.approx(0.5)
    assert len(result["recent_events"]) == 2


def test_authentication_section_empty(dashboard: SecurityDashboardAPI):
    result = dashboard.authentication()

    assert result["total_events"] == 0
    assert result["success_rate"] is None
    assert result["recent_events"] == []


def test_authorization_section(dashboard: SecurityDashboardAPI, audit_log: AuditLogService):
    _seed(audit_log)

    result = dashboard.authorization()

    assert result["total_events"] == 2
    assert result["denials"] == 1
    assert len(result["recent_events"]) == 2


def test_sessions_section(dashboard: SecurityDashboardAPI, audit_log: AuditLogService):
    _seed(audit_log)

    result = dashboard.sessions()

    assert result["total_created"] == 2
    assert result["total_terminated"] == 1
    assert result["active_sessions"] == 1
    assert len(result["recent_events"]) == 3


def test_analytics_section(dashboard: SecurityDashboardAPI, audit_log: AuditLogService):
    _seed(audit_log)

    result = dashboard.analytics()

    assert "summary" in result
    assert "trends" in result


def test_overview_combines_all_sections(dashboard: SecurityDashboardAPI, audit_log: AuditLogService):
    _seed(audit_log)

    result = dashboard.overview()

    assert set(result.keys()) == {"authentication", "authorization", "sessions", "analytics"}
    assert result["authentication"]["total_events"] == 2
    assert result["sessions"]["active_sessions"] == 1


def test_manifest_lists_datasets(dashboard: SecurityDashboardAPI):
    manifest = dashboard.manifest()

    assert manifest["datasets"] == ["authentication", "authorization", "sessions", "analytics"]
    assert "generated_at" in manifest


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    return TestClient(app)


def _seed_global(client: TestClient) -> None:
    from backend.security.audit_logs import get_audit_log_service

    get_audit_log_service().record(
        "Authentication", "api-dash-alice", "login", "attempt", outcome="success", timestamp=BASE_TIME
    )


def test_api_get_dashboard_overview(client: TestClient):
    _seed_global(client)

    response = client.get("/security/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"authentication", "authorization", "sessions", "analytics"}


def test_api_get_dashboard_authentication(client: TestClient):
    response = client.get("/security/dashboard/authentication")

    assert response.status_code == 200
    assert "total_events" in response.json()


def test_api_get_dashboard_authorization(client: TestClient):
    response = client.get("/security/dashboard/authorization")

    assert response.status_code == 200
    assert "denials" in response.json()


def test_api_get_dashboard_sessions(client: TestClient):
    response = client.get("/security/dashboard/sessions")

    assert response.status_code == 200
    assert "active_sessions" in response.json()


def test_api_get_dashboard_analytics(client: TestClient):
    response = client.get("/security/dashboard/analytics")

    assert response.status_code == 200
    assert "summary" in response.json()
