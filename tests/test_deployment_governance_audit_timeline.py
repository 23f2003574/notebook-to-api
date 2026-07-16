from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_timeline import (
    GovernanceIntegrityAuditTimelineService,
    GovernanceIntegrityAuditTimelineState,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    healthy: bool = True,
) -> GovernanceIntegrityAuditRecord:
    invalid_records = 0 if healthy else 1

    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if healthy
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=10,
        valid_records=10 - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def test_timeline_handles_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditTimelineService(repository)

    assert service.timeline() == ()


def test_timeline_returns_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = GovernanceIntegrityAuditTimelineService(repository)

    events = service.timeline()

    assert [event.audit_id for event in events] == ["C", "B", "A"]


def test_timeline_respects_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    service = GovernanceIntegrityAuditTimelineService(repository)

    events = service.timeline(limit=2)

    assert len(events) == 2


def test_timeline_maps_healthy_record() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", healthy=True))

    service = GovernanceIntegrityAuditTimelineService(repository)

    events = service.timeline()

    assert events[0].state is GovernanceIntegrityAuditTimelineState.HEALTHY


def test_timeline_maps_unhealthy_record() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", healthy=False))

    service = GovernanceIntegrityAuditTimelineService(repository)

    events = service.timeline()

    assert (
        events[0].state
        is GovernanceIntegrityAuditTimelineState.UNHEALTHY
    )


def test_timeline_maps_record_fields_directly() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    record = make_record(audit_id="A", healthy=False)

    repository.save(record)

    service = GovernanceIntegrityAuditTimelineService(repository)

    event = service.timeline()[0]

    assert event.audit_id == record.audit_id
    assert event.started_at == record.started_at
    assert event.completed_at == record.completed_at
    assert event.total_records == record.total_records
    assert event.invalid_records == record.invalid_records
    assert event.integrity_mismatches == record.integrity_mismatches


def test_timeline_rejects_non_positive_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditTimelineService(repository)

    with pytest.raises(ValueError):
        service.timeline(limit=0)


def test_timeline_event_to_dict() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", healthy=False))

    service = GovernanceIntegrityAuditTimelineService(repository)

    payload = service.timeline()[0].to_dict()

    assert payload["audit_id"] == "A"
    assert payload["state"] == "unhealthy"
    assert "started_at" in payload
    assert "completed_at" in payload


def test_runtime_builds_working_timeline_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "timeline-runtime.db"
        )
    )

    runtime.audit_history_repository.save(
        make_record(audit_id="A")
    )

    service = runtime.build_integrity_audit_timeline_service()

    events = service.timeline()

    assert events[0].audit_id == "A"
