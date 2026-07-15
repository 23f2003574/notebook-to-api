from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditAlreadyExistsError,
    GovernanceIntegrityAuditHistoryQuery,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    10,
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
    started_at = (
        BASE_TIME
        + timedelta(
            minutes=offset_minutes
        )
    )

    completed_at = (
        started_at
        + timedelta(
            seconds=2
        )
    )

    total_records = 10

    valid_records = (
        total_records
        - invalid_records
    )

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend=backend,
        started_at=started_at,
        completed_at=completed_at,
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=total_records,
        valid_records=valid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def test_repository_saves_and_restores_audit_record() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    record = make_record(
        audit_id="audit-001"
    )

    saved = repository.save(
        record
    )

    restored = repository.get_by_audit_id(
        record.audit_id
    )

    assert saved == record
    assert restored == record
    assert repository.count() == 1


def test_repository_rejects_duplicate_audit_id() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    record = make_record(
        audit_id="audit-duplicate"
    )

    repository.save(
        record
    )

    with pytest.raises(
        GovernanceIntegrityAuditAlreadyExistsError
    ):
        repository.save(
            record
        )


def test_repository_lists_audits_newest_first() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    repository.save(
        make_record(
            audit_id="audit-oldest",
            offset_minutes=0,
        )
    )

    repository.save(
        make_record(
            audit_id="audit-newest",
            offset_minutes=20,
        )
    )

    repository.save(
        make_record(
            audit_id="audit-middle",
            offset_minutes=10,
        )
    )

    records = repository.list()

    assert [
        record.audit_id
        for record in records
    ] == [
        "audit-newest",
        "audit-middle",
        "audit-oldest",
    ]


def test_repository_returns_latest_audit() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    repository.save(
        make_record(
            audit_id="audit-001",
            offset_minutes=0,
        )
    )

    repository.save(
        make_record(
            audit_id="audit-002",
            offset_minutes=30,
        )
    )

    latest = repository.latest()

    assert latest is not None

    assert (
        latest.audit_id
        == "audit-002"
    )


def test_repository_list_applies_limit() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    for index in range(
        5
    ):
        repository.save(
            make_record(
                audit_id=(
                    f"audit-{index}"
                ),
                offset_minutes=index,
            )
        )

    records = repository.list(
        limit=2
    )

    assert len(
        records
    ) == 2

    assert [
        record.audit_id
        for record in records
    ] == [
        "audit-4",
        "audit-3",
    ]


def test_repository_queries_by_outcome() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    repository.save(
        make_record(
            audit_id="audit-healthy",
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="audit-unhealthy",
            offset_minutes=10,
            invalid_records=2,
        )
    )

    records = repository.query(
        GovernanceIntegrityAuditHistoryQuery(
            outcome=(
                GovernanceIntegrityAuditOutcome.UNHEALTHY
            )
        )
    )

    assert len(
        records
    ) == 1

    assert (
        records[0].audit_id
        == "audit-unhealthy"
    )


def test_repository_queries_by_backend() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    repository.save(
        make_record(
            audit_id="audit-sqlite",
            backend="sqlite",
        )
    )

    repository.save(
        make_record(
            audit_id="audit-postgres",
            offset_minutes=10,
            backend="postgresql",
        )
    )

    records = repository.query(
        GovernanceIntegrityAuditHistoryQuery(
            backend="postgresql"
        )
    )

    assert len(
        records
    ) == 1

    assert (
        records[0].audit_id
        == "audit-postgres"
    )


def test_repository_queries_by_start_time_range() -> None:
    repository = (
        InMemoryGovernanceIntegrityAuditHistoryRepository()
    )

    for index in range(
        5
    ):
        repository.save(
            make_record(
                audit_id=(
                    f"audit-{index}"
                ),
                offset_minutes=(
                    index * 10
                ),
            )
        )

    records = repository.query(
        GovernanceIntegrityAuditHistoryQuery(
            started_at_or_after=(
                BASE_TIME
                + timedelta(
                    minutes=10
                )
            ),
            started_at_or_before=(
                BASE_TIME
                + timedelta(
                    minutes=30
                )
            ),
        )
    )

    assert [
        record.audit_id
        for record in records
    ] == [
        "audit-3",
        "audit-2",
        "audit-1",
    ]


def test_audit_record_rejects_inconsistent_total() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "valid_records \\+ invalid_records "
            "must equal total_records"
        ),
    ):
        GovernanceIntegrityAuditRecord(
            audit_id="audit-invalid-total",
            backend="sqlite",
            started_at=BASE_TIME,
            completed_at=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
            outcome=(
                GovernanceIntegrityAuditOutcome.HEALTHY
            ),
            total_records=10,
            valid_records=9,
            invalid_records=0,
            integrity_mismatches=0,
            missing_integrity_metadata=0,
            invalid_integrity_metadata=0,
            invalid_persisted_records=0,
        )


def test_audit_record_rejects_inconsistent_failure_breakdown() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "integrity failure counters must sum "
            "to invalid_records"
        ),
    ):
        GovernanceIntegrityAuditRecord(
            audit_id="audit-invalid-breakdown",
            backend="sqlite",
            started_at=BASE_TIME,
            completed_at=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
            outcome=(
                GovernanceIntegrityAuditOutcome.UNHEALTHY
            ),
            total_records=10,
            valid_records=8,
            invalid_records=2,
            integrity_mismatches=1,
            missing_integrity_metadata=0,
            invalid_integrity_metadata=0,
            invalid_persisted_records=0,
        )


def test_audit_record_rejects_outcome_that_disagrees_with_counters() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "audit outcome does not match "
            "the recorded integrity counters"
        ),
    ):
        GovernanceIntegrityAuditRecord(
            audit_id="audit-invalid-outcome",
            backend="sqlite",
            started_at=BASE_TIME,
            completed_at=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
            outcome=(
                GovernanceIntegrityAuditOutcome.HEALTHY
            ),
            total_records=10,
            valid_records=9,
            invalid_records=1,
            integrity_mismatches=1,
            missing_integrity_metadata=0,
            invalid_integrity_metadata=0,
            invalid_persisted_records=0,
        )
