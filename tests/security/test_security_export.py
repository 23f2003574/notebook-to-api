from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logger import SecurityAuditLogger
from backend.security.identity_registry import IdentityRegistry
from backend.security.dashboard import SecurityDashboardAPI as LegacySecurityDashboardAPI
from backend.security.security_metrics import SecurityAnalyticsService
from backend.security.ops_dashboard import SecurityDashboardAPI
from backend.security.security_export import (
    EXPORT_FORMATS,
    ExportManifest,
    InvalidExportFormatError,
    SecurityExport,
    SecurityExportService,
    UnknownExportError,
    router as security_export_router,
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
def dashboard_api(
    identity_registry: IdentityRegistry, audit_logger: SecurityAuditLogger, analytics_service: SecurityAnalyticsService
) -> SecurityDashboardAPI:
    return SecurityDashboardAPI(
        identity_registry=identity_registry, audit_logger=audit_logger, analytics_service=analytics_service
    )


@pytest.fixture
def legacy_dashboard_api() -> LegacySecurityDashboardAPI:
    return LegacySecurityDashboardAPI()


@pytest.fixture
def service(
    dashboard_api: SecurityDashboardAPI, legacy_dashboard_api: LegacySecurityDashboardAPI
) -> SecurityExportService:
    return SecurityExportService(dashboard_api=dashboard_api, legacy_dashboard_api=legacy_dashboard_api)


def test_export_identities_returns_json_by_default(
    service: SecurityExportService, identity_registry: IdentityRegistry
):
    identity_registry.register_identity("alice", "user", timestamp=BASE_TIME)

    export = service.export_identities(timestamp=BASE_TIME)

    assert isinstance(export, SecurityExport)
    assert export.dataset == "identities"
    assert export.format == "JSON"
    assert '"total": 1' in export.content


def test_export_identities_supports_yaml(service: SecurityExportService, identity_registry: IdentityRegistry):
    identity_registry.register_identity("alice", "user", timestamp=BASE_TIME)

    export = service.export_identities(export_format="YAML", timestamp=BASE_TIME)

    assert "total: 1" in export.content


def test_export_identities_supports_csv(service: SecurityExportService, identity_registry: IdentityRegistry):
    identity_registry.register_identity("alice", "user", timestamp=BASE_TIME)

    export = service.export_identities(export_format="CSV", timestamp=BASE_TIME)

    assert "key,value" in export.content


def test_export_rejects_invalid_format(service: SecurityExportService):
    with pytest.raises(InvalidExportFormatError):
        service.export_identities(export_format="XML")


def test_export_audit_logs(service: SecurityExportService, audit_logger: SecurityAuditLogger):
    audit_logger.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    export = service.export_audit_logs(timestamp=BASE_TIME)

    assert export.dataset == "audit"
    assert "session:1" in export.content


def test_export_analytics(service: SecurityExportService, analytics_service: SecurityAnalyticsService):
    analytics_service.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    export = service.export_analytics(timestamp=BASE_TIME)

    assert export.dataset == "analytics"
    assert "risk_score" in export.content


def test_export_all_returns_manifest_and_datasets(
    service: SecurityExportService, identity_registry: IdentityRegistry
):
    identity_registry.register_identity("alice", "user", timestamp=BASE_TIME)

    bundle = service.export_all(timestamp=BASE_TIME)

    assert set(bundle.keys()) == {"manifest", "identities", "audit", "analytics"}
    assert bundle["manifest"]["datasets"] == ["identities", "audit", "analytics"]
    assert bundle["manifest"]["legacy_datasets"] == [
        "authentication", "authorization", "sessions", "analytics",
    ]


def test_get_returns_stored_export(service: SecurityExportService, identity_registry: IdentityRegistry):
    export = service.export_identities(timestamp=BASE_TIME)

    found = service.get(export.export_id)

    assert found.export_id == export.export_id


def test_get_unknown_export_raises(service: SecurityExportService):
    with pytest.raises(UnknownExportError):
        service.get("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(security_export_router)
    return TestClient(app)


def test_api_export_identities(client: TestClient, service: SecurityExportService, monkeypatch, identity_registry: IdentityRegistry):
    from backend.security import security_export as security_export_module

    monkeypatch.setattr(security_export_module, "_security_export_service", service)
    identity_registry.register_identity("api-alice", "user", timestamp=BASE_TIME)

    response = client.get("/security/export/identities")

    assert response.status_code == 200
    assert response.json()["dataset"] == "identities"


def test_api_export_invalid_format_returns_422(client: TestClient, service: SecurityExportService, monkeypatch):
    from backend.security import security_export as security_export_module

    monkeypatch.setattr(security_export_module, "_security_export_service", service)

    response = client.get("/security/export/identities", params={"format": "XML"})

    assert response.status_code == 422


def test_api_export_audit(client: TestClient, service: SecurityExportService, monkeypatch, audit_logger: SecurityAuditLogger):
    from backend.security import security_export as security_export_module

    monkeypatch.setattr(security_export_module, "_security_export_service", service)
    audit_logger.record("Session", "api-alice", "session:1", "create", timestamp=BASE_TIME)

    response = client.get("/security/export/audit")

    assert response.status_code == 200
    assert response.json()["dataset"] == "audit"


def test_api_export_analytics(client: TestClient, service: SecurityExportService, monkeypatch):
    from backend.security import security_export as security_export_module

    monkeypatch.setattr(security_export_module, "_security_export_service", service)

    response = client.get("/security/export/analytics")

    assert response.status_code == 200
    assert response.json()["dataset"] == "analytics"


def test_api_export_all(client: TestClient, service: SecurityExportService, monkeypatch):
    from backend.security import security_export as security_export_module

    monkeypatch.setattr(security_export_module, "_security_export_service", service)

    response = client.get("/security/export/all")

    assert response.status_code == 200
    assert set(response.json().keys()) == {"manifest", "identities", "audit", "analytics"}
