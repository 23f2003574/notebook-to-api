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
