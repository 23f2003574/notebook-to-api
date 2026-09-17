from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditCurrentState,
    GovernanceIntegrityAuditStatisticsService,
    calculate_governance_integrity_audit_statistics,
)
from backend.observability.sqlite_deployment_governance_audit_history import (
    SQLiteGovernanceIntegrityAuditHistoryRepository,
)
from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    healthy: bool = True,
    total_records: int = 10,
    integrity_mismatches: int = 0,
    missing_integrity_metadata: int = 0,
    invalid_integrity_metadata: int = 0,
    invalid_persisted_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    if not healthy and (
        integrity_mismatches == 0
        and missing_integrity_metadata == 0
        and invalid_integrity_metadata == 0
        and invalid_persisted_records == 0
    ):
        integrity_mismatches = 1

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


def test_statistics_handles_empty_history() -> None:
    snapshot = calculate_governance_integrity_audit_statistics(())

    assert snapshot.total_audits == 0
    assert snapshot.health_rate is None
    assert (
        snapshot.current_state
        is GovernanceIntegrityAuditCurrentState.NO_HISTORY
    )
    assert snapshot.current_streak == 0
    assert snapshot.longest_healthy_streak == 0
    assert snapshot.longest_unhealthy_streak == 0
    assert snapshot.first_audit_started_at is None
    assert snapshot.latest_audit_started_at is None


def test_statistics_calculates_all_healthy_history() -> None:
    records = (
        make_record(audit_id="audit-4", healthy=True, offset_minutes=40),
        make_record(audit_id="audit-3", healthy=True, offset_minutes=30),
        make_record(audit_id="audit-2", healthy=True, offset_minutes=20),
        make_record(audit_id="audit-1", healthy=True, offset_minutes=10),
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    assert snapshot.total_audits == 4
    assert snapshot.healthy_audits == 4
    assert snapshot.unhealthy_audits == 0
    assert snapshot.health_rate == 1.0
    assert (
        snapshot.current_state
        is GovernanceIntegrityAuditCurrentState.HEALTHY
    )
    assert snapshot.current_streak == 4
    assert snapshot.longest_healthy_streak == 4
    assert snapshot.longest_unhealthy_streak == 0


def test_statistics_calculates_mixed_streaks() -> None:
    # Chronological (oldest -> newest): H H U U U H H H H U U
    # Repository order (newest -> oldest) is the reverse of that.
    chronological_health = [
        True, True, False, False, False,
        True, True, True, True, False, False,
    ]

    newest_first_health = list(reversed(chronological_health))

    records = tuple(
        make_record(
            audit_id=f"audit-{index}",
            healthy=healthy,
            offset_minutes=len(newest_first_health) - index,
        )
        for index, healthy in enumerate(newest_first_health)
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    assert (
        snapshot.current_state
        is GovernanceIntegrityAuditCurrentState.UNHEALTHY
    )
    assert snapshot.current_streak == 2
    assert snapshot.longest_healthy_streak == 4
    assert snapshot.longest_unhealthy_streak == 3


def test_statistics_calculates_health_rate() -> None:
    records = tuple(
        make_record(
            audit_id=f"audit-{index}",
            healthy=index < 8,
            offset_minutes=index,
        )
        for index in range(10)
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    assert snapshot.healthy_audits == 8
    assert snapshot.unhealthy_audits == 2
    assert snapshot.health_rate == pytest.approx(0.8)


def test_statistics_aggregates_failure_counts() -> None:
    records = (
        make_record(
            audit_id="audit-a",
            offset_minutes=0,
            total_records=100,
            integrity_mismatches=1,
        ),
        make_record(
            audit_id="audit-b",
            offset_minutes=10,
            total_records=50,
            integrity_mismatches=2,
            missing_integrity_metadata=1,
        ),
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    assert snapshot.total_records_checked == 150
    assert snapshot.total_invalid_records == 4
    assert snapshot.total_integrity_mismatches == 3
    assert snapshot.total_missing_integrity_metadata == 1
    assert snapshot.total_invalid_integrity_metadata == 0


def test_statistics_reports_first_and_latest_timestamps() -> None:
    oldest = make_record(
        audit_id="oldest", offset_minutes=0
    )
    newest = make_record(
        audit_id="newest", offset_minutes=20
    )

    # newest-first order, as the repository would return
    records = (newest, oldest)

    snapshot = calculate_governance_integrity_audit_statistics(records)

    assert snapshot.latest_audit_started_at == newest.started_at
    assert snapshot.first_audit_started_at == oldest.started_at


def test_statistics_service_calculates_from_repository() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(
                audit_id=f"audit-{index}", offset_minutes=index
            )
        )

    service = GovernanceIntegrityAuditStatisticsService(repository)

    snapshot = service.calculate()

    assert snapshot.total_audits == 5


def test_statistics_service_respects_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(10):
        repository.save(
            make_record(
                audit_id=f"audit-{index}", offset_minutes=index
            )
        )

    service = GovernanceIntegrityAuditStatisticsService(repository)

    snapshot = service.calculate(limit=3)

    assert snapshot.total_audits == 3


def test_statistics_service_rejects_non_positive_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditStatisticsService(repository)

    with pytest.raises(
        ValueError, match="limit must be greater than zero"
    ):
        service.calculate(limit=0)


def test_statistics_snapshot_rejects_inconsistent_totals() -> None:
    from backend.observability.deployment_governance_audit_statistics import (
        GovernanceIntegrityAuditStatisticsSnapshot,
    )

    with pytest.raises(
        ValueError,
        match="healthy_audits \\+ unhealthy_audits must equal total_audits",
    ):
        GovernanceIntegrityAuditStatisticsSnapshot(
            total_audits=3,
            healthy_audits=1,
            unhealthy_audits=1,
            health_rate=0.5,
            current_state=(
                GovernanceIntegrityAuditCurrentState.HEALTHY
            ),
            current_streak=1,
            longest_healthy_streak=1,
            longest_unhealthy_streak=1,
            first_audit_started_at=BASE_TIME,
            latest_audit_started_at=BASE_TIME,
            total_records_checked=0,
            total_invalid_records=0,
            total_integrity_mismatches=0,
            total_missing_integrity_metadata=0,
            total_invalid_integrity_metadata=0,
        )


def test_cross_backend_statistics_are_identical(tmp_path) -> None:
    memory_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "audit-statistics.db",
        )
    )

    sqlite_repository = SQLiteGovernanceIntegrityAuditHistoryRepository(
        database
    )

    health_sequence = [True, True, False, True]

    for index, healthy in enumerate(health_sequence):
        record = make_record(
            audit_id=f"audit-{index}",
            offset_minutes=index * 10,
            healthy=healthy,
        )

        memory_repository.save(record)
        sqlite_repository.save(record)

    memory_snapshot = GovernanceIntegrityAuditStatisticsService(
        memory_repository
    ).calculate()

    sqlite_snapshot = GovernanceIntegrityAuditStatisticsService(
        sqlite_repository
    ).calculate()

    assert memory_snapshot.to_dict() == sqlite_snapshot.to_dict()
