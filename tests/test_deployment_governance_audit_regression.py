from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionService,
    GovernanceIntegrityRegressionStatus,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    18,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    backend: str = "sqlite",
    invalid_records: int | None = None,
    integrity_mismatches: int = 0,
    missing_integrity_metadata: int = 0,
    invalid_integrity_metadata: int = 0,
    invalid_persisted_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    """
    invalid_records, when given without explicit category counts, is
    attributed entirely to integrity_mismatches for convenience.
    """

    if invalid_records is None:
        invalid_records = (
            integrity_mismatches
            + missing_integrity_metadata
            + invalid_integrity_metadata
            + invalid_persisted_records
        )
    elif (
        integrity_mismatches == 0
        and missing_integrity_metadata == 0
        and invalid_integrity_metadata == 0
        and invalid_persisted_records == 0
        and invalid_records > 0
    ):
        integrity_mismatches = invalid_records

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
        integrity_mismatches=integrity_mismatches,
        missing_integrity_metadata=missing_integrity_metadata,
        invalid_integrity_metadata=invalid_integrity_metadata,
        invalid_persisted_records=invalid_persisted_records,
    )


def test_regression_detection_handles_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert (
        snapshot.status
        is GovernanceIntegrityRegressionStatus.NO_HISTORY
    )

    assert snapshot.regression_detected is False
    assert snapshot.current_audit_id is None
    assert snapshot.baseline_audit_id is None


def test_single_healthy_audit_is_not_a_regression() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="healthy-1", invalid_records=0))

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert (
        snapshot.status
        is GovernanceIntegrityRegressionStatus.HEALTHY
    )

    assert snapshot.regression_detected is False


def test_single_unhealthy_audit_has_insufficient_baseline() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(audit_id="unhealthy-1", invalid_records=1)
    )

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert (
        snapshot.status
        is GovernanceIntegrityRegressionStatus.INSUFFICIENT_BASELINE
    )

    assert snapshot.regression_detected is False


def test_regression_detection_detects_healthy_to_unhealthy_transition() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="baseline-healthy",
            offset_minutes=0,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="current-unhealthy",
            offset_minutes=10,
            invalid_records=2,
        )
    )

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert (
        snapshot.status
        is GovernanceIntegrityRegressionStatus.REGRESSION
    )

    assert snapshot.regression_detected is True
    assert snapshot.baseline_audit_id == "baseline-healthy"
    assert snapshot.current_audit_id == "current-unhealthy"
    assert snapshot.invalid_record_delta == 2


def test_regression_detection_distinguishes_persistent_failure() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="unhealthy-old",
            offset_minutes=0,
            invalid_records=3,
        )
    )

    repository.save(
        make_record(
            audit_id="unhealthy-new",
            offset_minutes=10,
            invalid_records=2,
        )
    )

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert (
        snapshot.status
        is GovernanceIntegrityRegressionStatus.PERSISTENT_FAILURE
    )

    assert snapshot.regression_detected is False
    assert snapshot.invalid_record_delta == -1


def test_regression_detection_reports_healthy_after_recovery() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="unhealthy-old",
            offset_minutes=0,
            invalid_records=2,
        )
    )

    repository.save(
        make_record(
            audit_id="healthy-new",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert (
        snapshot.status
        is GovernanceIntegrityRegressionStatus.HEALTHY
    )

    assert snapshot.regression_detected is False
    assert snapshot.invalid_record_delta == -2


def test_regression_detection_only_compares_immediately_preceding_audit() -> None:
    # A run of consecutive unhealthy audits after one healthy baseline
    # should report exactly one REGRESSION followed by PERSISTENT_FAILURE,
    # not a fresh regression on every subsequent unhealthy audit.
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="healthy",
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

    first_snapshot = GovernanceIntegrityRegressionService(
        repository
    ).detect()

    assert (
        first_snapshot.status
        is GovernanceIntegrityRegressionStatus.REGRESSION
    )

    repository.save(
        make_record(
            audit_id="unhealthy-2",
            offset_minutes=20,
            invalid_records=1,
        )
    )

    second_snapshot = GovernanceIntegrityRegressionService(
        repository
    ).detect()

    assert (
        second_snapshot.status
        is GovernanceIntegrityRegressionStatus.PERSISTENT_FAILURE
    )

    assert second_snapshot.regression_detected is False


def test_regression_detection_identifies_new_failure_categories() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="baseline",
            offset_minutes=0,
        )
    )

    repository.save(
        make_record(
            audit_id="current",
            offset_minutes=10,
            integrity_mismatches=2,
            invalid_persisted_records=1,
        )
    )

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert snapshot.newly_introduced_failure_categories == (
        "integrity_mismatches",
        "invalid_persisted_records",
    )

    assert snapshot.integrity_mismatch_delta == 2
    assert snapshot.invalid_persisted_records_delta == 1


def test_regression_detection_reports_no_new_categories_when_unchanged() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="baseline",
            offset_minutes=0,
            integrity_mismatches=1,
        )
    )

    repository.save(
        make_record(
            audit_id="current",
            offset_minutes=10,
            integrity_mismatches=1,
        )
    )

    snapshot = GovernanceIntegrityRegressionService(repository).detect()

    assert snapshot.newly_introduced_failure_categories == ()
