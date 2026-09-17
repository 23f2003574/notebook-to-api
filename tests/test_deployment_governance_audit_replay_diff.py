from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_replay import (
    GovernanceIntegrityAuditReplayService,
)
from backend.observability.deployment_governance_audit_replay_diff import (
    GovernanceIntegrityAuditFieldDiff,
    GovernanceIntegrityAuditReplayDiff,
    GovernanceIntegrityAuditReplayDiffService,
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
    total_records: int = 10,
    integrity_mismatches: int = 0,
    missing_integrity_metadata: int = 0,
    invalid_integrity_metadata: int = 0,
    invalid_persisted_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    invalid_records = (
        integrity_mismatches
        + missing_integrity_metadata
        + invalid_integrity_metadata
        + invalid_persisted_records
    )

    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=total_records,
        valid_records=total_records - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=integrity_mismatches,
        missing_integrity_metadata=missing_integrity_metadata,
        invalid_integrity_metadata=invalid_integrity_metadata,
        invalid_persisted_records=invalid_persisted_records,
    )


def make_service(
    repository: InMemoryGovernanceIntegrityAuditHistoryRepository,
) -> GovernanceIntegrityAuditReplayDiffService:
    return GovernanceIntegrityAuditReplayDiffService(
        GovernanceIntegrityAuditReplayService(repository)
    )


def test_diff_rejects_inconsistent_changed_flag() -> None:
    with pytest.raises(
        ValueError,
        match="changed must match whether field_diffs is non-empty",
    ):
        GovernanceIntegrityAuditReplayDiff(
            previous_audit_id="A",
            current_audit_id="B",
            changed=True,
            field_diffs=(),
        )


def test_compare_same_audit_has_no_diff() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A"))

    service = make_service(repository)

    diff = service.compare("A", "A")

    assert diff.changed is False
    assert diff.field_diffs == ()


def test_compare_reports_invalid_records_change() -> None:
    # total_records is held fixed and both audits stay unhealthy, so the
    # only fields the record invariants allow to change here are
    # invalid_records, valid_records, and integrity_mismatches (their
    # source subfield) -- "healthy" cannot change since 2 and 5 are both
    # non-zero, and total_records is held constant.
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(audit_id="A", integrity_mismatches=2)
    )
    repository.save(
        make_record(
            audit_id="B", offset_minutes=10, integrity_mismatches=5
        )
    )

    service = make_service(repository)

    diff = service.compare("A", "B")

    assert diff.changed is True

    changed_fields = {
        field_diff.field: field_diff for field_diff in diff.field_diffs
    }

    assert changed_fields["invalid_records"].previous == 2
    assert changed_fields["invalid_records"].current == 5
    assert "healthy" not in changed_fields
    assert "total_records" not in changed_fields


def test_compare_reports_multiple_changed_fields() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(audit_id="A", integrity_mismatches=0)
    )
    repository.save(
        make_record(
            audit_id="B",
            offset_minutes=10,
            integrity_mismatches=1,
            missing_integrity_metadata=1,
        )
    )

    service = make_service(repository)

    diff = service.compare("A", "B")

    changed_fields = {
        field_diff.field for field_diff in diff.field_diffs
    }

    assert "invalid_records" in changed_fields
    assert "integrity_mismatches" in changed_fields
    assert "healthy" in changed_fields


def test_compare_latest_uses_two_most_recent_audits() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = make_service(repository)

    diff = service.compare_latest()

    assert diff.previous_audit_id == "B"
    assert diff.current_audit_id == "C"


def test_compare_latest_raises_for_insufficient_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A"))

    service = make_service(repository)

    with pytest.raises(LookupError):
        service.compare_latest()


def test_compare_latest_raises_for_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(repository)

    with pytest.raises(LookupError):
        service.compare_latest()


def test_compare_raises_for_missing_audit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A"))

    service = make_service(repository)

    with pytest.raises(KeyError):
        service.compare("A", "missing")


def test_field_diff_to_dict() -> None:
    field_diff = GovernanceIntegrityAuditFieldDiff(
        field="invalid_records", previous=2, current=5
    )

    assert field_diff.to_dict() == {
        "field": "invalid_records",
        "previous": 2,
        "current": 5,
    }


def test_diff_to_dict() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(audit_id="A", integrity_mismatches=1)
    )
    repository.save(
        make_record(
            audit_id="B", offset_minutes=10, integrity_mismatches=2
        )
    )

    service = make_service(repository)

    diff = service.compare("A", "B")

    payload = diff.to_dict()

    assert payload["previous_audit_id"] == "A"
    assert payload["current_audit_id"] == "B"
    assert payload["changed"] is True
    assert isinstance(payload["field_diffs"], list)
    assert len(payload["field_diffs"]) >= 1


def test_runtime_builds_working_diff_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "diff-runtime.db"
        )
    )

    runtime.audit_history_repository.save(
        make_record(audit_id="A", integrity_mismatches=0)
    )
    runtime.audit_history_repository.save(
        make_record(
            audit_id="B", offset_minutes=10, integrity_mismatches=1
        )
    )

    service = runtime.build_integrity_audit_replay_diff_service()

    diff = service.compare_latest()

    assert diff.previous_audit_id == "A"
    assert diff.current_audit_id == "B"
    assert diff.changed is True
