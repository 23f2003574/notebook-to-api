from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logger import SecurityAuditLogger
from backend.security.security_metrics import (
    SecurityAnalyticsService,
    SecurityMetrics,
    SecuritySnapshot,
    router as security_metrics_router,
)

BASE_TIME = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def audit_logger() -> SecurityAuditLogger:
    return SecurityAuditLogger()


@pytest.fixture
def service(audit_logger: SecurityAuditLogger) -> SecurityAnalyticsService:
    return SecurityAnalyticsService(audit_logger=audit_logger)


def test_record_delegates_to_audit_logger(service: SecurityAnalyticsService, audit_logger: SecurityAuditLogger):
    service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    assert len(audit_logger.query()) == 1


def test_summary_computes_login_success_rate(service: SecurityAnalyticsService):
    service.record("Authentication", "alice", "user:alice", "login", outcome="success", timestamp=BASE_TIME)
    service.record("Authentication", "bob", "user:bob", "login", outcome="failure", severity="Warning", timestamp=BASE_TIME)
    service.record("Authentication", "carol", "user:carol", "login", outcome="success", timestamp=BASE_TIME)

    metrics = service.summary()

    assert isinstance(metrics, SecurityMetrics)
    assert metrics.login_success_rate == pytest.approx(2 / 3)
    assert metrics.failed_auth_attempts == 1


def test_summary_with_no_auth_events_has_none_success_rate(service: SecurityAnalyticsService):
    metrics = service.summary()

    assert metrics.login_success_rate is None
    assert metrics.failed_auth_attempts == 0


def test_summary_counts_permission_denials(service: SecurityAnalyticsService):
    service.record("Authorization", "alice", "orders", "read", outcome="denied", severity="Warning", timestamp=BASE_TIME)
    service.record("Authorization", "alice", "orders", "write", outcome="success", timestamp=BASE_TIME)

    metrics = service.summary()

    assert metrics.permission_denials == 1


def test_summary_counts_api_key_usage(service: SecurityAnalyticsService):
    service.record("API Key", "alice", "key:1", "validate", timestamp=BASE_TIME)
    service.record("API Key", "alice", "key:1", "validate", timestamp=BASE_TIME)

    metrics = service.summary()

    assert metrics.api_key_usage == 2


def test_summary_respects_window(service: SecurityAnalyticsService):
    service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    service.record(
        "Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME + timedelta(days=2)
    )

    metrics = service.summary(window_start=BASE_TIME + timedelta(days=1))

    assert metrics.failed_auth_attempts == 0
    assert metrics.audit_event_count == 1


def test_trends_buckets_events_by_period(service: SecurityAnalyticsService):
    service.record(
        "Authentication", "alice", "user:alice", "login", outcome="failure", severity="Warning", timestamp=BASE_TIME
    )
    service.record(
        "Authentication", "alice", "user:alice", "login", outcome="failure", severity="Warning",
        timestamp=BASE_TIME + timedelta(days=1),
    )

    snapshots = service.trends(bucket_seconds=86400.0)

    assert len(snapshots) == 2
    assert all(isinstance(snapshot, SecuritySnapshot) for snapshot in snapshots)
    assert snapshots[0].failed_auth_attempts == 1
    assert snapshots[1].failed_auth_attempts == 1


def test_trends_rejects_non_positive_bucket(service: SecurityAnalyticsService):
    with pytest.raises(ValueError):
        service.trends(bucket_seconds=0)


def test_trends_empty_when_no_events(service: SecurityAnalyticsService):
    assert service.trends() == []


def test_risk_score_zero_with_no_events(service: SecurityAnalyticsService):
    assert service.risk_score() == 0.0


def test_risk_score_weighs_critical_events_heavily(service: SecurityAnalyticsService):
    service.record(
        "Authentication", "alice", "user:alice", "login",
        outcome="failure", severity="Critical", timestamp=BASE_TIME,
    )

    score = service.risk_score()

    assert score == pytest.approx(2 + 10)


def test_risk_score_capped_at_maximum(service: SecurityAnalyticsService):
    for _ in range(50):
        service.record(
            "Authentication", "alice", "user:alice", "login",
            outcome="failure", severity="Critical", timestamp=BASE_TIME,
        )

    assert service.risk_score() == 100.0


def test_export_combines_summary_trends_and_risk(service: SecurityAnalyticsService):
    service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    exported = service.export()

    assert "summary" in exported
    assert "trends" in exported
    assert "risk_score" in exported


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(security_metrics_router)
    return TestClient(app)


def test_api_analytics_export(client: TestClient, service: SecurityAnalyticsService, monkeypatch):
    from backend.security import security_metrics as security_metrics_module

    monkeypatch.setattr(security_metrics_module, "_security_analytics_service", service)
    service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    response = client.get("/security/analytics")

    assert response.status_code == 200
    assert "risk_score" in response.json()


def test_api_analytics_summary(client: TestClient, service: SecurityAnalyticsService, monkeypatch):
    from backend.security import security_metrics as security_metrics_module

    monkeypatch.setattr(security_metrics_module, "_security_analytics_service", service)
    service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    response = client.get("/security/analytics/summary")

    assert response.status_code == 200
    assert response.json()["login_success_rate"] == 1.0


def test_api_analytics_trends(client: TestClient, service: SecurityAnalyticsService, monkeypatch):
    from backend.security import security_metrics as security_metrics_module

    monkeypatch.setattr(security_metrics_module, "_security_analytics_service", service)
    service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    response = client.get("/security/analytics/trends")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_analytics_trends_invalid_bucket_returns_422(
    client: TestClient, service: SecurityAnalyticsService, monkeypatch
):
    from backend.security import security_metrics as security_metrics_module

    monkeypatch.setattr(security_metrics_module, "_security_analytics_service", service)

    response = client.get("/security/analytics/trends", params={"bucket_seconds": 0})

    assert response.status_code == 422


def test_api_analytics_risk(client: TestClient, service: SecurityAnalyticsService, monkeypatch):
    from backend.security import security_metrics as security_metrics_module

    monkeypatch.setattr(security_metrics_module, "_security_analytics_service", service)
    service.record(
        "Authentication", "alice", "user:alice", "login",
        outcome="failure", severity="Warning", timestamp=BASE_TIME,
    )

    response = client.get("/security/analytics/risk")

    assert response.status_code == 200
    assert response.json()["risk_score"] == pytest.approx(2 + 1)
