from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logs import AuditLogService
from backend.security.security_analytics import (
    SecurityAnalyticsService,
    SecurityMetrics,
    SecurityTrend,
    router as security_analytics_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def audit_log() -> AuditLogService:
    return AuditLogService()


@pytest.fixture
def service(audit_log: AuditLogService) -> SecurityAnalyticsService:
    return SecurityAnalyticsService(audit_log=audit_log)


def test_record_delegates_to_audit_log(service: SecurityAnalyticsService, audit_log: AuditLogService):
    event = service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)

    assert audit_log.get(event.event_id).actor == "alice"


def test_summary_computes_authentication_success_rate(service: SecurityAnalyticsService):
    service.record("Authentication", "alice", "login", "attempt", outcome="success", timestamp=BASE_TIME)
    service.record("Authentication", "bob", "login", "attempt", outcome="success", timestamp=BASE_TIME)
    service.record("Authentication", "eve", "login", "attempt", outcome="failure", timestamp=BASE_TIME)

    metrics = service.summary()

    assert isinstance(metrics, SecurityMetrics)
    assert metrics.authentication_failures == 1
    assert metrics.authentication_success_rate == pytest.approx(2 / 3)


def test_summary_with_no_authentication_events_has_none_rate(service: SecurityAnalyticsService):
    metrics = service.summary()

    assert metrics.authentication_success_rate is None
    assert metrics.authentication_failures == 0


def test_summary_counts_permission_denials(service: SecurityAnalyticsService):
    service.record("Authorization", "alice", "notebooks", "check", outcome="success", timestamp=BASE_TIME)
    service.record("Authorization", "bob", "notebooks", "check", outcome="denied", timestamp=BASE_TIME)

    metrics = service.summary()

    assert metrics.permission_denials == 1


def test_summary_computes_active_sessions(service: SecurityAnalyticsService):
    service.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:2", "create", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:1", "terminate", timestamp=BASE_TIME)

    metrics = service.summary()

    assert metrics.active_sessions == 1


def test_summary_counts_secret_rotations(service: SecurityAnalyticsService):
    service.record("Configuration", "alice", "secret:api-key", "rotate", timestamp=BASE_TIME)
    service.record("Configuration", "alice", "secret:api-key", "rotate", timestamp=BASE_TIME)

    metrics = service.summary()

    assert metrics.secret_rotations == 2


def test_summary_respects_time_window(service: SecurityAnalyticsService):
    service.record(
        "Authentication", "alice", "login", "attempt", outcome="failure", timestamp=BASE_TIME
    )
    service.record(
        "Authentication",
        "alice",
        "login",
        "attempt",
        outcome="success",
        timestamp=BASE_TIME + timedelta(hours=2),
    )

    metrics = service.summary(window_start=BASE_TIME + timedelta(hours=1))

    assert metrics.authentication_failures == 0
    assert metrics.authentication_success_rate == pytest.approx(1.0)


def test_trends_buckets_events_over_time(service: SecurityAnalyticsService):
    service.record("Authentication", "alice", "login", "attempt", outcome="failure", timestamp=BASE_TIME)
    service.record(
        "Authentication",
        "alice",
        "login",
        "attempt",
        outcome="failure",
        timestamp=BASE_TIME + timedelta(days=1, hours=1),
    )

    trends = service.trends(bucket_seconds=86400)

    assert len(trends) == 2
    assert isinstance(trends[0], SecurityTrend)
    assert trends[0].authentication_failures == 1
    assert trends[1].authentication_failures == 1


def test_trends_empty_when_no_events(service: SecurityAnalyticsService):
    assert service.trends() == []


def test_trends_rejects_non_positive_bucket(service: SecurityAnalyticsService):
    with pytest.raises(ValueError):
        service.trends(bucket_seconds=0)


def test_export_returns_summary_and_trends(service: SecurityAnalyticsService):
    service.record("Authentication", "alice", "login", "attempt", outcome="failure", timestamp=BASE_TIME)

    report = service.export()

    assert "summary" in report
    assert "trends" in report
    assert report["summary"]["authentication_failures"] == 1
    assert len(report["trends"]) == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(security_analytics_router)
    return TestClient(app)


def test_api_get_analytics_export(client: TestClient):
    from backend.security.security_analytics import get_security_analytics_service

    get_security_analytics_service().record(
        "Authentication", "api-alice", "login", "attempt", outcome="failure", timestamp=BASE_TIME
    )

    response = client.get("/security/analytics")

    assert response.status_code == 200
    assert "summary" in response.json()
    assert "trends" in response.json()


def test_api_get_summary(client: TestClient):
    response = client.get("/security/analytics/summary")

    assert response.status_code == 200
    assert "authentication_failures" in response.json()


def test_api_get_summary_invalid_window_returns_422(client: TestClient):
    response = client.get("/security/analytics/summary", params={"window_start": "not-a-date"})

    assert response.status_code == 422


def test_api_get_trends(client: TestClient):
    response = client.get("/security/analytics/trends")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_get_trends_invalid_bucket_returns_422(client: TestClient):
    response = client.get("/security/analytics/trends", params={"bucket_seconds": 0})

    assert response.status_code == 422
