from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logger import (
    AuditEvent,
    AuditFilter,
    InvalidEventTypeError,
    InvalidSeverityError,
    SecurityAuditLogger,
    UnknownEventError,
    router as audit_logger_router,
)

BASE_TIME = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def logger() -> SecurityAuditLogger:
    return SecurityAuditLogger()


def test_record_returns_event(logger: SecurityAuditLogger):
    event = logger.record(
        "Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME
    )

    assert isinstance(event, AuditEvent)
    assert event.sequence == 0
    assert event.severity == "Info"
    assert event.previous_hash == "0" * 64


def test_record_rejects_unknown_event_type(logger: SecurityAuditLogger):
    with pytest.raises(InvalidEventTypeError):
        logger.record("Configuration", "alice", "user:alice", "login")


def test_record_rejects_unknown_severity(logger: SecurityAuditLogger):
    with pytest.raises(InvalidSeverityError):
        logger.record("Authentication", "alice", "user:alice", "login", severity="Fatal")


def test_record_chains_hashes(logger: SecurityAuditLogger):
    first = logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    second = logger.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    assert second.previous_hash == first.record_hash
    assert second.sequence == 1


def test_get_returns_event_by_id(logger: SecurityAuditLogger):
    event = logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    found = logger.get(event.event_id)

    assert found.event_id == event.event_id


def test_get_unknown_event_raises(logger: SecurityAuditLogger):
    with pytest.raises(UnknownEventError):
        logger.get("does-not-exist")


def test_query_filters_by_event_type(logger: SecurityAuditLogger):
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    logger.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    results = logger.query(AuditFilter(event_type="Session"))

    assert len(results) == 1
    assert results[0].event_type == "Session"


def test_query_filters_by_actor(logger: SecurityAuditLogger):
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    logger.record("Authentication", "bob", "user:bob", "login", timestamp=BASE_TIME)

    results = logger.query(AuditFilter(actor="bob"))

    assert len(results) == 1
    assert results[0].actor == "bob"


def test_query_filters_by_severity(logger: SecurityAuditLogger):
    logger.record(
        "Authentication", "alice", "user:alice", "login",
        outcome="failure", severity="Warning", timestamp=BASE_TIME,
    )
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    results = logger.query(AuditFilter(severity="Warning"))

    assert len(results) == 1
    assert results[0].outcome == "failure"


def test_query_filters_by_time_range(logger: SecurityAuditLogger):
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    logger.record(
        "Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME + timedelta(hours=2)
    )

    results = logger.query(AuditFilter(since=BASE_TIME + timedelta(hours=1)))

    assert len(results) == 1


def test_count_matches_query_length(logger: SecurityAuditLogger):
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    logger.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    assert logger.count() == 2
    assert logger.count(AuditFilter(event_type="Session")) == 1


def test_export_returns_serializable_dicts(logger: SecurityAuditLogger):
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    exported = logger.export()

    assert isinstance(exported, list)
    assert exported[0]["actor"] == "alice"


def test_purge_removes_events_before_cutoff(logger: SecurityAuditLogger):
    logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)
    logger.record(
        "Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME + timedelta(days=30)
    )

    purged = logger.purge(before=BASE_TIME + timedelta(days=1))

    assert purged == 1
    assert len(logger.query()) == 1


def test_purge_removed_events_are_unreachable(logger: SecurityAuditLogger):
    event = logger.record("Authentication", "alice", "user:alice", "login", timestamp=BASE_TIME)

    logger.purge(before=BASE_TIME + timedelta(days=1))

    with pytest.raises(UnknownEventError):
        logger.get(event.event_id)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(audit_logger_router)
    return TestClient(app)


def test_api_record_event(client: TestClient, logger: SecurityAuditLogger, monkeypatch):
    from backend.security import audit_logger as audit_logger_module

    monkeypatch.setattr(audit_logger_module, "_security_audit_logger", logger)

    response = client.post(
        "/security/audit",
        json={"event_type": "Authentication", "actor": "alice", "resource": "user:alice", "action": "login"},
    )

    assert response.status_code == 200
    assert response.json()["severity"] == "Info"


def test_api_record_event_invalid_type_returns_422(client: TestClient, logger: SecurityAuditLogger, monkeypatch):
    from backend.security import audit_logger as audit_logger_module

    monkeypatch.setattr(audit_logger_module, "_security_audit_logger", logger)

    response = client.post(
        "/security/audit",
        json={"event_type": "Nope", "actor": "alice", "resource": "user:alice", "action": "login"},
    )

    assert response.status_code == 422


def test_api_query_events(client: TestClient, logger: SecurityAuditLogger, monkeypatch):
    from backend.security import audit_logger as audit_logger_module

    monkeypatch.setattr(audit_logger_module, "_security_audit_logger", logger)
    logger.record("Authentication", "api-alice", "user:api-alice", "login", timestamp=BASE_TIME)

    response = client.get("/security/audit", params={"actor": "api-alice"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_event(client: TestClient, logger: SecurityAuditLogger, monkeypatch):
    from backend.security import audit_logger as audit_logger_module

    monkeypatch.setattr(audit_logger_module, "_security_audit_logger", logger)
    event = logger.record("Authentication", "api-alice", "user:api-alice", "login", timestamp=BASE_TIME)

    response = client.get(f"/security/audit/{event.event_id}")

    assert response.status_code == 200
    assert response.json()["event_id"] == event.event_id


def test_api_get_event_unknown_returns_404(client: TestClient, logger: SecurityAuditLogger, monkeypatch):
    from backend.security import audit_logger as audit_logger_module

    monkeypatch.setattr(audit_logger_module, "_security_audit_logger", logger)

    response = client.get("/security/audit/does-not-exist")

    assert response.status_code == 404


def test_api_export_events(client: TestClient, logger: SecurityAuditLogger, monkeypatch):
    from backend.security import audit_logger as audit_logger_module

    monkeypatch.setattr(audit_logger_module, "_security_audit_logger", logger)
    logger.record("Session", "api-alice", "session:1", "create", timestamp=BASE_TIME)

    response = client.post("/security/audit/export", json={"event_type": "Session"})

    assert response.status_code == 200
    assert len(response.json()) == 1
