from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.audit_logs import (
    AuditEvent,
    AuditLogService,
    AuditQuery,
    InvalidEventTypeError,
    UnknownEventError,
    router as audit_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def service() -> AuditLogService:
    return AuditLogService()


def test_record_creates_event(service: AuditLogService):
    event = service.record(
        "Authentication", "alice", "login", "attempt", timestamp=BASE_TIME
    )

    assert isinstance(event, AuditEvent)
    assert event.sequence == 0
    assert event.previous_hash == "0" * 64
    assert event.record_hash != ""


def test_record_rejects_unknown_event_type(service: AuditLogService):
    with pytest.raises(InvalidEventTypeError):
        service.record("NotAType", "alice", "login", "attempt")


def test_record_chains_hashes(service: AuditLogService):
    first = service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    second = service.record(
        "Session", "alice", "session:1", "create", timestamp=BASE_TIME + timedelta(seconds=1)
    )

    assert second.sequence == 1
    assert second.previous_hash == first.record_hash


def test_get_returns_recorded_event(service: AuditLogService):
    event = service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)

    assert service.get(event.event_id).event_id == event.event_id


def test_get_unknown_event_raises(service: AuditLogService):
    with pytest.raises(UnknownEventError):
        service.get("does-not-exist")


def test_query_filters_by_event_type(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    results = service.query(AuditQuery(event_type="Session"))

    assert len(results) == 1
    assert results[0].event_type == "Session"


def test_query_filters_by_actor(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record("Authentication", "bob", "login", "attempt", timestamp=BASE_TIME)

    results = service.query(AuditQuery(actor="bob"))

    assert len(results) == 1
    assert results[0].actor == "bob"


def test_query_filters_by_time_range(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record(
        "Authentication", "alice", "login", "attempt", timestamp=BASE_TIME + timedelta(hours=2)
    )

    results = service.query(AuditQuery(since=BASE_TIME + timedelta(hours=1)))

    assert len(results) == 1


def test_query_without_filter_returns_all(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    assert len(service.query()) == 2


def test_export_returns_dicts(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)

    exported = service.export()

    assert isinstance(exported, list)
    assert exported[0]["actor"] == "alice"
    assert "record_hash" in exported[0]


def test_verify_returns_true_for_untampered_log(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    assert service.verify() is True


def test_verify_returns_true_for_empty_log(service: AuditLogService):
    assert service.verify() is True


def test_verify_detects_tampered_event(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    tampered = replace(service._events[0], action="tampered")
    service._events[0] = tampered
    service._by_id[tampered.event_id] = tampered

    assert service.verify() is False


def test_verify_detects_broken_chain_link(service: AuditLogService):
    service.record("Authentication", "alice", "login", "attempt", timestamp=BASE_TIME)
    service.record("Session", "alice", "session:1", "create", timestamp=BASE_TIME)

    tampered = replace(service._events[1], previous_hash="f" * 64)
    service._events[1] = tampered
    service._by_id[tampered.event_id] = tampered

    assert service.verify() is False


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(audit_router)
    return TestClient(app)


def test_api_record_event(client: TestClient):
    response = client.post(
        "/security/audit",
        json={
            "event_type": "Authentication",
            "actor": "api-alice",
            "resource": "login",
            "action": "attempt",
        },
    )

    assert response.status_code == 200
    assert response.json()["actor"] == "api-alice"


def test_api_record_event_invalid_type_returns_422(client: TestClient):
    response = client.post(
        "/security/audit",
        json={"event_type": "NotAType", "actor": "api-alice", "resource": "login", "action": "attempt"},
    )

    assert response.status_code == 422


def test_api_query_events(client: TestClient):
    client.post(
        "/security/audit",
        json={
            "event_type": "Authentication",
            "actor": "api-query-user",
            "resource": "login",
            "action": "attempt",
        },
    )

    response = client.get("/security/audit", params={"actor": "api-query-user"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_event(client: TestClient):
    create_response = client.post(
        "/security/audit",
        json={
            "event_type": "Authentication",
            "actor": "api-get-user",
            "resource": "login",
            "action": "attempt",
        },
    )
    event_id = create_response.json()["event_id"]

    response = client.get(f"/security/audit/{event_id}")

    assert response.status_code == 200
    assert response.json()["event_id"] == event_id


def test_api_get_unknown_event_returns_404(client: TestClient):
    response = client.get("/security/audit/does-not-exist")

    assert response.status_code == 404
