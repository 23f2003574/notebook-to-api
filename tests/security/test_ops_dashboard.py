from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logger import SecurityAuditLogger
from backend.security.identity_registry import IdentityRegistry
from backend.security.security_analytics import SecurityAnalyticsService as LegacySecurityAnalyticsService
from backend.security.security_metrics import SecurityAnalyticsService
from backend.security.ops_dashboard import (
    SecurityDashboardAPI,
    router as ops_dashboard_router,
)

BASE_TIME = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def identity_registry() -> IdentityRegistry:
    return IdentityRegistry()


@pytest.fixture
def audit_logger() -> SecurityAuditLogger:
    return SecurityAuditLogger()


@pytest.fixture
def analytics_service(audit_logger: SecurityAuditLogger) -> SecurityAnalyticsService:
    return SecurityAnalyticsService(audit_logger=audit_logger)


@pytest.fixture
def legacy_analytics_service() -> LegacySecurityAnalyticsService:
    return LegacySecurityAnalyticsService()


@pytest.fixture
def dashboard(
    identity_registry: IdentityRegistry,
    audit_logger: SecurityAuditLogger,
    analytics_service: SecurityAnalyticsService,
    legacy_analytics_service: LegacySecurityAnalyticsService,
) -> SecurityDashboardAPI:
    return SecurityDashboardAPI(
        identity_registry=identity_registry,
        audit_logger=audit_logger,
        analytics_service=analytics_service,
        legacy_analytics_service=legacy_analytics_service,
    )


def test_identities_counts_by_identity_type(dashboard: SecurityDashboardAPI, identity_registry: IdentityRegistry):
    identity_registry.register_identity("alice", "user", timestamp=BASE_TIME)
    identity_registry.register_identity("svc-1", "service", timestamp=BASE_TIME)
    identity_registry.register_identity("bob", "user", timestamp=BASE_TIME)

    result = dashboard.identities()

    assert result["total"] == 3
    assert result["by_identity_type"] == {"user": 2, "service": 1}


def test_identities_reports_login_success_rate(
    dashboard: SecurityDashboardAPI, analytics_service: SecurityAnalyticsService
):
    analytics_service.record("Authentication", "alice", "user:alice", "login", outcome="success", timestamp=BASE_TIME)
    analytics_service.record(
        "Authentication", "bob", "user:bob", "login", outcome="failure", severity="Warning", timestamp=BASE_TIME
    )

    result = dashboard.identities()

    assert result["login_success_rate"] == pytest.approx(0.5)
    assert result["failed_auth_attempts"] == 1


def test_audits_groups_by_severity(dashboard: SecurityDashboardAPI, audit_logger: SecurityAuditLogger):
    audit_logger.record("Authentication", "alice", "user:alice", "login", severity="Info", timestamp=BASE_TIME)
    audit_logger.record(
        "Authentication", "bob", "user:bob", "login", outcome="failure", severity="Warning", timestamp=BASE_TIME
    )

    result = dashboard.audits()

    assert result["total"] == 2
    assert result["by_severity"] == {"Info": 1, "Warning": 1}
    assert len(result["recent_events"]) == 2


def test_audits_respects_limit(dashboard: SecurityDashboardAPI, audit_logger: SecurityAuditLogger):
    for _ in range(5):
        audit_logger.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    result = dashboard.audits(limit=2)

    assert result["total"] == 5
    assert len(result["recent_events"]) == 2


def test_analytics_combines_metrics_risk_and_legacy_indicators(
    dashboard: SecurityDashboardAPI, analytics_service: SecurityAnalyticsService
):
    analytics_service.record(
        "Authentication", "alice", "user:alice", "login", outcome="failure", severity="Critical", timestamp=BASE_TIME
    )

    result = dashboard.analytics()

    assert "metrics" in result
    assert "risk_score" in result
    assert "legacy_risk_indicators" in result
    assert result["risk_score"] > 0


def test_overview_combines_all_sections(dashboard: SecurityDashboardAPI, identity_registry: IdentityRegistry):
    identity_registry.register_identity("alice", "user", timestamp=BASE_TIME)

    result = dashboard.overview()

    assert set(result.keys()) == {"identities", "audit", "analytics"}
    assert result["identities"]["total"] == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ops_dashboard_router)
    return TestClient(app)


def test_api_dashboard_overview(client: TestClient, dashboard: SecurityDashboardAPI, monkeypatch):
    from backend.security import ops_dashboard as ops_dashboard_module

    monkeypatch.setattr(ops_dashboard_module, "_security_dashboard_api", dashboard)

    response = client.get("/security/dashboard")

    assert response.status_code == 200
    assert set(response.json().keys()) == {"identities", "audit", "analytics"}


def test_api_dashboard_authentication(
    client: TestClient, dashboard: SecurityDashboardAPI, identity_registry: IdentityRegistry, monkeypatch
):
    from backend.security import ops_dashboard as ops_dashboard_module

    monkeypatch.setattr(ops_dashboard_module, "_security_dashboard_api", dashboard)
    identity_registry.register_identity("api-alice", "user", timestamp=BASE_TIME)

    response = client.get("/security/dashboard/authentication")

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_api_dashboard_audit(
    client: TestClient, dashboard: SecurityDashboardAPI, audit_logger: SecurityAuditLogger, monkeypatch
):
    from backend.security import ops_dashboard as ops_dashboard_module

    monkeypatch.setattr(ops_dashboard_module, "_security_dashboard_api", dashboard)
    audit_logger.record("Session", "api-alice", "session:1", "create", timestamp=BASE_TIME)

    response = client.get("/security/dashboard/audit")

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_api_dashboard_analytics(client: TestClient, dashboard: SecurityDashboardAPI, monkeypatch):
    from backend.security import ops_dashboard as ops_dashboard_module

    monkeypatch.setattr(ops_dashboard_module, "_security_dashboard_api", dashboard)

    response = client.get("/security/dashboard/analytics")

    assert response.status_code == 200
    assert "risk_score" in response.json()
