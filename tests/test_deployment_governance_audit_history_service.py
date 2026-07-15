from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_history_service import (
    GovernanceIntegrityAuditHistoryService,
    serialize_governance_integrity_audit_record,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    16,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    backend: str = "sqlite",
    invalid_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend=backend,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
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


def test_history_service_builds_summary() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="healthy-1", invalid_records=0))

    repository.save(
        make_record(
            audit_id="healthy-2",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="unhealthy-1",
            offset_minutes=20,
            invalid_records=1,
        )
    )

    service = GovernanceIntegrityAuditHistoryService(repository)

    summary = service.summary()

    assert summary.total_audits == 3
    assert summary.healthy_audits == 2
    assert summary.unhealthy_audits == 1

    assert summary.latest_audit is not None
    assert summary.latest_audit.audit_id == "unhealthy-1"


def test_history_service_searches_with_filters() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="healthy", invalid_records=0))

    repository.save(
        make_record(
            audit_id="unhealthy",
            offset_minutes=10,
            invalid_records=1,
        )
    )

    service = GovernanceIntegrityAuditHistoryService(repository)

    result = service.search(
        outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
        limit=10,
    )

    assert len(result.records) == 1
    assert result.records[0].audit_id == "unhealthy"

    # summary always describes the whole history, not the filtered result
    assert result.summary.total_audits == 2


def test_history_service_summary_empty_repository() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditHistoryService(repository)

    summary = service.summary()

    assert summary.total_audits == 0
    assert summary.has_history is False
    assert summary.latest_audit is None


def test_audit_record_serialization_is_json_safe() -> None:
    record = make_record(audit_id="audit-json", invalid_records=1)

    payload = serialize_governance_integrity_audit_record(record)

    assert payload["audit_id"] == "audit-json"
    assert payload["outcome"] == "unhealthy"
    assert isinstance(payload["started_at"], str)
    assert isinstance(payload["completed_at"], str)
    assert payload["invalid_records"] == 1

    serialized = json.dumps(payload)
    assert isinstance(serialized, str)


def test_history_result_to_dict_contains_summary_and_records() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="audit-1"))

    service = GovernanceIntegrityAuditHistoryService(repository)

    result = service.search()

    payload = result.to_dict()

    assert "summary" in payload
    assert "records" in payload
    assert len(payload["records"]) == 1

    serialized = json.dumps(payload)
    assert isinstance(serialized, str)
