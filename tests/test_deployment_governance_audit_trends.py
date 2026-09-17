from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_trends import (
    GovernanceIntegrityAuditTrendDirection,
    GovernanceIntegrityAuditTrendService,
    GovernanceIntegrityAuditTrendSnapshot,
    calculate_governance_integrity_audit_streak,
    determine_governance_integrity_audit_trend_direction,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    17,
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


def test_trend_analysis_reports_insufficient_data_for_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditTrendService(repository)

    snapshot = service.analyze()

    assert snapshot.sample_size == 0
    assert snapshot.health_rate is None
    assert snapshot.failure_rate is None
    assert snapshot.current_outcome is None
    assert snapshot.previous_outcome is None
    assert snapshot.current_streak == 0

    assert (
        snapshot.direction
        is GovernanceIntegrityAuditTrendDirection.INSUFFICIENT_DATA
    )


def test_trend_analysis_requires_two_audits_for_direction() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="audit-1", invalid_records=0))

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze()

    assert snapshot.sample_size == 1

    assert (
        snapshot.current_outcome
        is GovernanceIntegrityAuditOutcome.HEALTHY
    )

    assert snapshot.previous_outcome is None
    assert snapshot.current_streak == 1

    assert (
        snapshot.direction
        is GovernanceIntegrityAuditTrendDirection.INSUFFICIENT_DATA
    )


def test_trend_analysis_detects_improving_transition() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="older-unhealthy",
            offset_minutes=0,
            invalid_records=1,
        )
    )

    repository.save(
        make_record(
            audit_id="newer-healthy",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze()

    assert (
        snapshot.direction
        is GovernanceIntegrityAuditTrendDirection.IMPROVING
    )

    assert (
        snapshot.current_outcome
        is GovernanceIntegrityAuditOutcome.HEALTHY
    )

    assert (
        snapshot.previous_outcome
        is GovernanceIntegrityAuditOutcome.UNHEALTHY
    )


def test_trend_analysis_detects_degrading_transition() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="older-healthy",
            offset_minutes=0,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="newer-unhealthy",
            offset_minutes=10,
            invalid_records=2,
        )
    )

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze()

    assert (
        snapshot.direction
        is GovernanceIntegrityAuditTrendDirection.DEGRADING
    )


def test_trend_analysis_detects_stable_healthy_state() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="healthy-1",
            offset_minutes=0,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="healthy-2",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze()

    assert (
        snapshot.direction
        is GovernanceIntegrityAuditTrendDirection.STABLE
    )

    assert snapshot.current_streak == 2


def test_trend_analysis_calculates_current_streak() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="healthy-old",
            offset_minutes=0,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="unhealthy-1",
            offset_minutes=10,
            invalid_records=1,
        )
    )

    repository.save(
        make_record(
            audit_id="unhealthy-2",
            offset_minutes=20,
            invalid_records=2,
        )
    )

    repository.save(
        make_record(
            audit_id="unhealthy-3",
            offset_minutes=30,
            invalid_records=1,
        )
    )

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze()

    assert (
        snapshot.current_outcome
        is GovernanceIntegrityAuditOutcome.UNHEALTHY
    )

    assert snapshot.current_streak == 3


def test_trend_analysis_calculates_recent_health_rates() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="healthy-1",
            offset_minutes=0,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="healthy-2",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="healthy-3",
            offset_minutes=20,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="unhealthy-1",
            offset_minutes=30,
            invalid_records=1,
        )
    )

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze()

    assert snapshot.sample_size == 4

    assert snapshot.health_rate == pytest.approx(0.75)
    assert snapshot.failure_rate == pytest.approx(0.25)


def test_trend_analysis_only_uses_requested_recent_window() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="old-unhealthy",
            offset_minutes=0,
            invalid_records=1,
        )
    )

    repository.save(
        make_record(
            audit_id="recent-healthy-1",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="recent-healthy-2",
            offset_minutes=20,
            invalid_records=0,
        )
    )

    snapshot = GovernanceIntegrityAuditTrendService(repository).analyze(
        window=2
    )

    assert snapshot.sample_size == 2
    assert snapshot.healthy_audits == 2
    assert snapshot.unhealthy_audits == 0
    assert snapshot.health_rate == 1.0
    assert snapshot.current_streak == 2


def test_trend_analysis_rejects_non_positive_window() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditTrendService(repository)

    with pytest.raises(
        ValueError, match="window must be greater than zero"
    ):
        service.analyze(window=0)


def test_determine_direction_with_fewer_than_two_records() -> None:
    assert (
        determine_governance_integrity_audit_trend_direction(())
        is GovernanceIntegrityAuditTrendDirection.INSUFFICIENT_DATA
    )


def test_calculate_streak_with_no_records() -> None:
    assert calculate_governance_integrity_audit_streak(()) == 0


def test_snapshot_rejects_inconsistent_counters() -> None:
    with pytest.raises(
        ValueError,
        match="healthy_audits \\+ unhealthy_audits must equal sample_size",
    ):
        GovernanceIntegrityAuditTrendSnapshot(
            sample_size=3,
            healthy_audits=1,
            unhealthy_audits=1,
            health_rate=0.5,
            failure_rate=0.5,
            current_outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
            previous_outcome=None,
            current_streak=1,
            direction=(
                GovernanceIntegrityAuditTrendDirection
                .INSUFFICIENT_DATA
            ),
        )


def test_snapshot_rejects_current_outcome_on_empty_sample() -> None:
    with pytest.raises(
        ValueError,
        match="current_outcome must be absent for an empty sample",
    ):
        GovernanceIntegrityAuditTrendSnapshot(
            sample_size=0,
            healthy_audits=0,
            unhealthy_audits=0,
            health_rate=None,
            failure_rate=None,
            current_outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
            previous_outcome=None,
            current_streak=0,
            direction=(
                GovernanceIntegrityAuditTrendDirection
                .INSUFFICIENT_DATA
            ),
        )
