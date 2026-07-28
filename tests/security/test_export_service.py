import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logs import AuditLogService
from backend.security.dashboard import SecurityDashboardAPI
from backend.security.security_analytics import SecurityAnalyticsService
from backend.security.export_service import (
    InvalidExportFormatError,
    SecurityExportResult,
    SecurityExportService,
    UnknownExportError,
    router as export_router,
)

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


@pytest.fixture
def service(
    audit_log: AuditLogService,
    analytics_service: SecurityAnalyticsService,
    dashboard: SecurityDashboardAPI,
) -> SecurityExportService:
    return SecurityExportService(
        audit_log=audit_log, analytics_service=analytics_service, dashboard_api=dashboard
    )


def _seed(audit_log: AuditLogService) -> None:
    audit_log.record("Authentication", "alice", "login", "attempt", outcome="success", timestamp=BASE_TIME)
    audit_log.record("Authentication", "eve", "login", "attempt", outcome="failure", timestamp=BASE_TIME)
    audit_log.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)


def test_export_audit_json(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_audit(export_format="JSON", timestamp=BASE_TIME)

    assert isinstance(result, SecurityExportResult)
    assert result.request.format == "JSON"
    assert result.record_count == 3
    parsed = json.loads(result.content)
    assert len(parsed) == 3


def test_export_audit_csv(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_audit(export_format="CSV", timestamp=BASE_TIME)

    lines = result.content.strip().split("\r\n")
    assert lines[0].startswith("event_id,")
    assert len(lines) == 4  # header + 3 records


def test_export_audit_yaml(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_audit(export_format="YAML", timestamp=BASE_TIME)

    assert "actor: alice" in result.content
    assert "event_type: Authentication" in result.content


def test_export_audit_filters_by_event_type(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_audit(event_type="Session", timestamp=BASE_TIME)

    parsed = json.loads(result.content)
    assert len(parsed) == 1
    assert parsed[0]["event_type"] == "Session"


def test_export_audit_filters_by_time_range(service: SecurityExportService, audit_log: AuditLogService):
    audit_log.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    audit_log.record(
        "Authentication", "alice", "login", "attempt", timestamp=BASE_TIME + timedelta(hours=2)
    )

    result = service.export_audit(since=BASE_TIME + timedelta(hours=1), timestamp=BASE_TIME)

    parsed = json.loads(result.content)
    assert len(parsed) == 1


def test_export_audit_invalid_format_raises(service: SecurityExportService):
    with pytest.raises(InvalidExportFormatError):
        service.export_audit(export_format="XML")


def test_export_sessions(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_sessions(timestamp=BASE_TIME)

    parsed = json.loads(result.content)
    assert len(parsed) == 1
    assert parsed[0]["event_type"] == "Session"


def test_export_analytics(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_analytics(timestamp=BASE_TIME)

    parsed = json.loads(result.content)
    assert "summary" in parsed
    assert "trends" in parsed
    assert result.record_count == 1


def test_export_dashboard(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)

    result = service.export_dashboard(timestamp=BASE_TIME)

    parsed = json.loads(result.content)
    assert set(parsed.keys()) == {"authentication", "authorization", "sessions", "analytics"}


def test_get_returns_stored_export(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)
    result = service.export_audit(timestamp=BASE_TIME)

    fetched = service.get(result.export_id)

    assert fetched.export_id == result.export_id
    assert fetched.content == result.content


def test_get_unknown_export_raises(service: SecurityExportService):
    with pytest.raises(UnknownExportError):
        service.get("does-not-exist")


def test_stream_reconstructs_full_content(service: SecurityExportService, audit_log: AuditLogService):
    _seed(audit_log)
    result = service.export_audit(export_format="YAML", timestamp=BASE_TIME)

    chunks = list(service.stream(result.export_id, chunk_size=2))

    assert "".join(chunks) == result.content


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(export_router)
    return TestClient(app)


def _seed_global(actor_suffix: str) -> None:
    from backend.security.audit_logs import get_audit_log_service

    get_audit_log_service().record(
        "Authentication", f"api-export-{actor_suffix}", "login", "attempt", timestamp=BASE_TIME
    )


def test_api_create_audit_export(client: TestClient):
    _seed_global("json")

    response = client.post("/security/export", json={"dataset": "audit", "format": "JSON"})

    assert response.status_code == 200
    assert response.json()["request"]["dataset"] == "audit"


def test_api_create_export_unknown_dataset_returns_422(client: TestClient):
    response = client.post("/security/export", json={"dataset": "not-a-dataset", "format": "JSON"})

    assert response.status_code == 422


def test_api_create_export_invalid_format_returns_422(client: TestClient):
    response = client.post("/security/export", json={"dataset": "audit", "format": "XML"})

    assert response.status_code == 422


def test_api_export_dashboard(client: TestClient):
    response = client.post("/security/export/dashboard", json={"format": "JSON"})

    assert response.status_code == 200
    assert response.json()["request"]["dataset"] == "dashboard"


def test_api_get_export(client: TestClient):
    create_response = client.post("/security/export", json={"dataset": "sessions", "format": "JSON"})
    export_id = create_response.json()["export_id"]

    response = client.get(f"/security/export/{export_id}")

    assert response.status_code == 200
    assert response.json()["export_id"] == export_id


def test_api_get_unknown_export_returns_404(client: TestClient):
    response = client.get("/security/export/does-not-exist")

    assert response.status_code == 404
