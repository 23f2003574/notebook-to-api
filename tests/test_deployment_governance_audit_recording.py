from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_recording import (
    GovernanceIntegrityAuditRecordMapper,
    GovernanceIntegrityAuditRecordingService,
)
from backend.observability.deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionService,
)
from backend.observability.deployment_governance_audit_retention import (
    GovernanceIntegrityAuditAutomaticRetentionConfig,
    GovernanceIntegrityAuditRetentionService,
)
from backend.observability.deployment_governance_integrity_audit import (
    GovernanceTraceIntegrityAuditFinding,
    GovernanceTraceIntegrityAuditReport,
    GovernanceTraceIntegrityAuditStatus,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    14,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_audit_report(
    *,
    total_records: int,
    valid_records: int,
    invalid_records: int,
    integrity_mismatches: int,
    missing_integrity_metadata: int,
    invalid_integrity_metadata: int,
    invalid_persisted_records: int,
) -> GovernanceTraceIntegrityAuditReport:
    assert valid_records + invalid_records == total_records

    assert (
        integrity_mismatches
        + missing_integrity_metadata
        + invalid_integrity_metadata
        + invalid_persisted_records
        == invalid_records
    )

    findings: list[GovernanceTraceIntegrityAuditFinding] = []

    for index in range(valid_records):
        findings.append(
            GovernanceTraceIntegrityAuditFinding(
                trace_id=f"trace-valid-{index}",
                status=GovernanceTraceIntegrityAuditStatus.VALID,
            )
        )

    status_counts = (
        (
            GovernanceTraceIntegrityAuditStatus.INTEGRITY_MISMATCH,
            integrity_mismatches,
        ),
        (
            GovernanceTraceIntegrityAuditStatus.MISSING_INTEGRITY_METADATA,
            missing_integrity_metadata,
        ),
        (
            GovernanceTraceIntegrityAuditStatus.INVALID_INTEGRITY_METADATA,
            invalid_integrity_metadata,
        ),
        (
            GovernanceTraceIntegrityAuditStatus.INVALID_PERSISTED_RECORD,
            invalid_persisted_records,
        ),
    )

    for status, count in status_counts:
        for index in range(count):
            findings.append(
                GovernanceTraceIntegrityAuditFinding(
                    trace_id=f"trace-{status.value}-{index}",
                    status=status,
                )
            )

    return GovernanceTraceIntegrityAuditReport(
        started_at=BASE_TIME,
        completed_at=BASE_TIME + timedelta(seconds=2),
        findings=tuple(findings),
    )


class StubAuditExecutor:
    def __init__(self, report: GovernanceTraceIntegrityAuditReport) -> None:
        self.report = report
        self.batch_sizes: list[int] = []

    def audit(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceTraceIntegrityAuditReport:
        self.batch_sizes.append(batch_size)
        return self.report


class FailingAuditExecutor:
    def audit(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceTraceIntegrityAuditReport:
        raise RuntimeError("simulated audit failure")


class FailingHistoryRepository(
    InMemoryGovernanceIntegrityAuditHistoryRepository
):
    def save(self, record):  # type: ignore[override]
        raise RuntimeError("simulated history persistence failure")


def make_report_at(
    started_at: datetime,
    *,
    invalid_records: int = 0,
) -> GovernanceTraceIntegrityAuditReport:
    status = (
        GovernanceTraceIntegrityAuditStatus.VALID
        if invalid_records == 0
        else GovernanceTraceIntegrityAuditStatus.INTEGRITY_MISMATCH
    )

    findings = tuple(
        GovernanceTraceIntegrityAuditFinding(
            trace_id=f"trace-{index}",
            status=status,
        )
        for index in range(max(invalid_records, 1))
    )

    return GovernanceTraceIntegrityAuditReport(
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        findings=findings,
    )


class SequentialAuditExecutor:
    """
    Returns a fresh report with a strictly increasing started_at on each
    call, so successive audit_and_record() calls produce distinct,
    deterministically ordered historical records instead of all sharing
    one fixed timestamp.
    """

    def __init__(
        self,
        *,
        invalid_records_sequence: tuple[int, ...] = (),
    ) -> None:
        self._invalid_records_sequence = invalid_records_sequence
        self._call_count = 0
        self.batch_sizes: list[int] = []

    def audit(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceTraceIntegrityAuditReport:
        self.batch_sizes.append(batch_size)

        invalid_records = (
            self._invalid_records_sequence[self._call_count]
            if self._call_count < len(self._invalid_records_sequence)
            else 0
        )

        started_at = BASE_TIME + timedelta(
            minutes=self._call_count * 10
        )

        self._call_count += 1

        return make_report_at(
            started_at, invalid_records=invalid_records
        )


class FailingRetentionService:
    def prune(self, policy, *, apply: bool = False):
        raise RuntimeError("retention failed")


def fail_if_called() -> str:
    raise AssertionError(
        "audit ID factory must not run before a successful audit"
    )


def test_mapper_converts_healthy_report_to_history_record() -> None:
    report = make_audit_report(
        total_records=10,
        valid_records=10,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    mapper = GovernanceIntegrityAuditRecordMapper(backend="sqlite")

    record = mapper.from_report(report, audit_id="audit-healthy")

    assert record.audit_id == "audit-healthy"
    assert record.backend == "sqlite"
    assert record.outcome is GovernanceIntegrityAuditOutcome.HEALTHY
    assert record.healthy is True
    assert record.total_records == 10
    assert record.invalid_records == 0
    assert record.started_at == report.started_at
    assert record.completed_at == report.completed_at


def test_mapper_preserves_unhealthy_audit_counters() -> None:
    report = make_audit_report(
        total_records=10,
        valid_records=6,
        invalid_records=4,
        integrity_mismatches=1,
        missing_integrity_metadata=1,
        invalid_integrity_metadata=1,
        invalid_persisted_records=1,
    )

    mapper = GovernanceIntegrityAuditRecordMapper(backend="sqlite")

    record = mapper.from_report(report, audit_id="audit-unhealthy")

    assert record.outcome is GovernanceIntegrityAuditOutcome.UNHEALTHY
    assert record.healthy is False
    assert record.invalid_records == 4
    assert record.integrity_mismatches == 1
    assert record.missing_integrity_metadata == 1
    assert record.invalid_integrity_metadata == 1
    assert record.invalid_persisted_records == 1


def test_mapper_rejects_empty_audit_id() -> None:
    report = make_audit_report(
        total_records=0,
        valid_records=0,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    mapper = GovernanceIntegrityAuditRecordMapper(backend="sqlite")

    with pytest.raises(ValueError, match="audit_id must not be empty"):
        mapper.from_report(report, audit_id="   ")


def test_recording_service_executes_and_persists_audit() -> None:
    report = make_audit_report(
        total_records=5,
        valid_records=5,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    executor = StubAuditExecutor(report)

    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=executor,
        history_repository=history_repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(backend="sqlite"),
        audit_id_factory=lambda: "audit-recorded",
    )

    result = service.audit_and_record(batch_size=250)

    assert executor.batch_sizes == [250]
    assert result.report is report
    assert result.audit_id == "audit-recorded"
    assert result.healthy is True
    assert history_repository.count() == 1
    assert (
        history_repository.get_by_audit_id("audit-recorded")
        == result.record
    )


def test_recording_service_persists_unhealthy_audit() -> None:
    report = make_audit_report(
        total_records=10,
        valid_records=8,
        invalid_records=2,
        integrity_mismatches=2,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=StubAuditExecutor(report),
        history_repository=history_repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(backend="sqlite"),
        audit_id_factory=lambda: "audit-unhealthy-recorded",
    )

    result = service.audit_and_record()

    assert result.healthy is False
    assert result.record.invalid_records == 2
    assert history_repository.count() == 1


def test_recording_service_does_not_persist_when_audit_fails() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=FailingAuditExecutor(),
        history_repository=history_repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(backend="sqlite"),
        audit_id_factory=fail_if_called,
    )

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        service.audit_and_record()

    assert history_repository.count() == 0


def test_recording_service_propagates_history_persistence_failure() -> None:
    report = make_audit_report(
        total_records=0,
        valid_records=0,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=StubAuditExecutor(report),
        history_repository=FailingHistoryRepository(),
        record_mapper=GovernanceIntegrityAuditRecordMapper(backend="sqlite"),
        audit_id_factory=lambda: "audit-save-failure",
    )

    with pytest.raises(
        RuntimeError,
        match="simulated history persistence failure",
    ):
        service.audit_and_record()


def test_recording_service_rejects_empty_generated_audit_id() -> None:
    report = make_audit_report(
        total_records=0,
        valid_records=0,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=StubAuditExecutor(report),
        history_repository=history_repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(backend="sqlite"),
        audit_id_factory=lambda: "   ",
    )

    with pytest.raises(
        ValueError,
        match=(
            "audit_id_factory returned an empty audit identifier"
        ),
    ):
        service.audit_and_record()

    assert history_repository.count() == 0


def test_recording_service_rejects_invalid_batch_size() -> None:
    report = make_audit_report(
        total_records=0,
        valid_records=0,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    executor = StubAuditExecutor(report)

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=executor,
        history_repository=InMemoryGovernanceIntegrityAuditHistoryRepository(),
        record_mapper=GovernanceIntegrityAuditRecordMapper(backend="sqlite"),
    )

    with pytest.raises(
        ValueError,
        match="batch_size must be greater than zero",
    ):
        service.audit_and_record(batch_size=0)

    assert executor.batch_sizes == []


def test_recording_service_rejects_enabled_retention_without_service() -> None:
    with pytest.raises(
        ValueError,
        match="automatic retention requires a retention service",
    ):
        GovernanceIntegrityAuditRecordingService(
            audit_executor=SequentialAuditExecutor(),
            history_repository=(
                InMemoryGovernanceIntegrityAuditHistoryRepository()
            ),
            record_mapper=GovernanceIntegrityAuditRecordMapper(
                backend="sqlite"
            ),
            automatic_retention=(
                GovernanceIntegrityAuditAutomaticRetentionConfig(
                    enabled=True, max_records=2
                )
            ),
        )


def test_automatic_retention_bounds_history_after_recording() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    retention_service = GovernanceIntegrityAuditRetentionService(
        repository
    )

    recording_service = GovernanceIntegrityAuditRecordingService(
        audit_executor=SequentialAuditExecutor(),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
        retention_service=retention_service,
        automatic_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig(
                enabled=True, max_records=3
            )
        ),
    )

    for _ in range(5):
        result = recording_service.audit_and_record()

        assert repository.count() <= 3
        assert result.retention is not None
        assert result.retention.applied is True

    assert repository.count() == 3


def test_automatic_retention_never_prunes_the_just_recorded_audit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    recording_service = GovernanceIntegrityAuditRecordingService(
        audit_executor=SequentialAuditExecutor(),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
        retention_service=(
            GovernanceIntegrityAuditRetentionService(repository)
        ),
        automatic_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig(
                enabled=True, max_records=2
            )
        ),
    )

    result = None

    for _ in range(5):
        result = recording_service.audit_and_record()

        remaining_ids = {
            record.audit_id for record in repository.list()
        }

        assert result.audit_id in remaining_ids


def test_disabled_automatic_retention_does_not_prune_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    recording_service = GovernanceIntegrityAuditRecordingService(
        audit_executor=SequentialAuditExecutor(),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
        retention_service=(
            GovernanceIntegrityAuditRetentionService(repository)
        ),
        automatic_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig.disabled()
        ),
    )

    for _ in range(5):
        result = recording_service.audit_and_record()

        assert result.retention is None

    assert repository.count() == 5


def test_recording_service_defaults_to_disabled_automatic_retention() -> None:
    # No retention_service and no automatic_retention supplied at all:
    # this must behave exactly as it did before automatic retention
    # existed.
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    recording_service = GovernanceIntegrityAuditRecordingService(
        audit_executor=SequentialAuditExecutor(),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
    )

    for _ in range(3):
        result = recording_service.audit_and_record()
        assert result.retention is None

    assert repository.count() == 3


def test_automatic_retention_prunes_by_age() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    now = BASE_TIME + timedelta(days=100)

    # Pre-seed old history directly, bypassing the recording flow, so we
    # can control ages precisely rather than relying on real time.
    repository.save(
        _make_retention_test_record(
            audit_id="ancient",
            started_at=now - timedelta(days=90),
        )
    )

    repository.save(
        _make_retention_test_record(
            audit_id="old",
            started_at=now - timedelta(days=40),
        )
    )

    retention_service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: now
    )

    class FixedTimeAuditExecutor:
        def audit(self, *, batch_size: int = 500):
            return make_report_at(now)

    recording_service = GovernanceIntegrityAuditRecordingService(
        audit_executor=FixedTimeAuditExecutor(),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
        retention_service=retention_service,
        automatic_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig(
                enabled=True, max_age_days=30
            )
        ),
    )

    result = recording_service.audit_and_record()

    remaining_ids = {
        record.audit_id for record in repository.list()
    }

    assert remaining_ids == {result.audit_id}
    assert result.retention is not None
    assert result.retention.deleted_records == 2


def test_automatic_retention_failure_is_not_silently_ignored() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditRecordingService(
        audit_executor=SequentialAuditExecutor(),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
        retention_service=FailingRetentionService(),
        automatic_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig(
                enabled=True, max_records=10
            )
        ),
    )

    with pytest.raises(RuntimeError, match="retention failed"):
        service.audit_and_record()

    # The audit itself was already persisted before retention failed: this
    # is documented, non-atomic lifecycle behavior, not a bug.
    assert repository.count() == 1


def test_automatic_retention_preserves_history_required_for_regression_detection() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    recording_service = GovernanceIntegrityAuditRecordingService(
        audit_executor=SequentialAuditExecutor(
            invalid_records_sequence=(0, 0, 2)
        ),
        history_repository=repository,
        record_mapper=GovernanceIntegrityAuditRecordMapper(
            backend="sqlite"
        ),
        retention_service=(
            GovernanceIntegrityAuditRetentionService(repository)
        ),
        automatic_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig(
                enabled=True, max_records=2
            )
        ),
    )

    recording_service.audit_and_record()  # A: healthy
    recording_service.audit_and_record()  # B: healthy
    recording_service.audit_and_record()  # C: unhealthy

    assert repository.count() == 2

    regression = GovernanceIntegrityRegressionService(
        repository
    ).detect()

    assert regression.regression_detected is True


def _make_retention_test_record(*, audit_id: str, started_at: datetime):
    from backend.observability.deployment_governance_audit_history import (
        GovernanceIntegrityAuditOutcome as _Outcome,
        GovernanceIntegrityAuditRecord,
    )

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=_Outcome.HEALTHY,
        total_records=10,
        valid_records=10,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )
