from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_recording import (
    GovernanceIntegrityAuditRecordingResult,
)
from backend.observability.deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionSnapshot,
    GovernanceIntegrityRegressionStatus,
)
from backend.observability.deployment_governance_check import (
    GovernanceIntegrityCheckPolicy,
    GovernanceIntegrityCheckService,
    GovernanceIntegrityCheckStatus,
)
from backend.observability.deployment_governance_integrity_audit import (
    GovernanceTraceIntegrityAuditFinding,
    GovernanceTraceIntegrityAuditReport,
    GovernanceTraceIntegrityAuditStatus,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    19,
    0,
    0,
    tzinfo=timezone.utc,
)


class StubRecordingService:
    def __init__(self, *, healthy: bool, audit_id: str = "audit-check") -> None:
        self.batch_sizes: list[int] = []

        if healthy:
            findings = (
                GovernanceTraceIntegrityAuditFinding(
                    trace_id="trace-1",
                    status=GovernanceTraceIntegrityAuditStatus.VALID,
                ),
            )
        else:
            findings = (
                GovernanceTraceIntegrityAuditFinding(
                    trace_id="trace-1",
                    status=(
                        GovernanceTraceIntegrityAuditStatus
                        .INTEGRITY_MISMATCH
                    ),
                ),
            )

        report = GovernanceTraceIntegrityAuditReport(
            started_at=BASE_TIME,
            completed_at=BASE_TIME + timedelta(seconds=1),
            findings=findings,
        )

        record = GovernanceIntegrityAuditRecord(
            audit_id=audit_id,
            backend="sqlite",
            started_at=report.started_at,
            completed_at=report.completed_at,
            outcome=(
                GovernanceIntegrityAuditOutcome.HEALTHY
                if healthy
                else GovernanceIntegrityAuditOutcome.UNHEALTHY
            ),
            total_records=report.total_records,
            valid_records=report.valid_records,
            invalid_records=report.invalid_records,
            integrity_mismatches=report.integrity_mismatches,
            missing_integrity_metadata=report.missing_integrity_metadata,
            invalid_integrity_metadata=report.invalid_integrity_metadata,
            invalid_persisted_records=report.invalid_persisted_records,
        )

        self._result = GovernanceIntegrityAuditRecordingResult(
            report=report,
            record=record,
        )

    def audit_and_record(
        self,
        *,
        batch_size: int = 500,
    ) -> GovernanceIntegrityAuditRecordingResult:
        self.batch_sizes.append(batch_size)
        return self._result


def make_regression_snapshot(
    *, regression_detected: bool
) -> GovernanceIntegrityRegressionSnapshot:
    status = (
        GovernanceIntegrityRegressionStatus.REGRESSION
        if regression_detected
        else GovernanceIntegrityRegressionStatus.HEALTHY
    )

    return GovernanceIntegrityRegressionSnapshot(
        status=status,
        regression_detected=regression_detected,
        current_audit_id="audit-check",
        baseline_audit_id="audit-baseline",
        current_outcome=(
            GovernanceIntegrityAuditOutcome.UNHEALTHY
            if regression_detected
            else GovernanceIntegrityAuditOutcome.HEALTHY
        ),
        baseline_outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        current_invalid_records=1 if regression_detected else 0,
        baseline_invalid_records=0,
        invalid_record_delta=1 if regression_detected else 0,
        integrity_mismatch_delta=1 if regression_detected else 0,
        missing_integrity_metadata_delta=0,
        invalid_integrity_metadata_delta=0,
        invalid_persisted_records_delta=0,
        newly_introduced_failure_categories=(
            ("integrity_mismatches",) if regression_detected else ()
        ),
    )


INSUFFICIENT_BASELINE_SNAPSHOT = GovernanceIntegrityRegressionSnapshot(
    status=GovernanceIntegrityRegressionStatus.INSUFFICIENT_BASELINE,
    regression_detected=False,
    current_audit_id="audit-check",
    baseline_audit_id=None,
    current_outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
    baseline_outcome=None,
    current_invalid_records=1,
    baseline_invalid_records=None,
    invalid_record_delta=None,
    integrity_mismatch_delta=None,
    missing_integrity_metadata_delta=None,
    invalid_integrity_metadata_delta=None,
    invalid_persisted_records_delta=None,
    newly_introduced_failure_categories=(),
)


class StubRegressionService:
    def __init__(
        self,
        *,
        regression_detected: bool = False,
        snapshot: GovernanceIntegrityRegressionSnapshot | None = None,
    ) -> None:
        self._snapshot = (
            snapshot
            if snapshot is not None
            else make_regression_snapshot(
                regression_detected=regression_detected
            )
        )

    def detect(self) -> GovernanceIntegrityRegressionSnapshot:
        return self._snapshot


def test_healthy_integrity_check_passes() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=True),
        regression_service=StubRegressionService(regression_detected=False),
    )

    result = service.check()

    assert result.status is GovernanceIntegrityCheckStatus.PASSED
    assert result.passed is True
    assert result.audit_healthy is True


def test_regression_only_policy_fails_on_new_regression() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=False),
        regression_service=StubRegressionService(regression_detected=True),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REGRESSION_ONLY
    )

    assert (
        result.status
        is GovernanceIntegrityCheckStatus.REGRESSION_DETECTED
    )

    assert result.passed is False


def test_regression_only_policy_allows_non_regressive_unhealthy_state() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=False),
        regression_service=StubRegressionService(regression_detected=False),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REGRESSION_ONLY
    )

    assert result.status is GovernanceIntegrityCheckStatus.PASSED
    assert result.passed is True
    assert result.audit_healthy is False


def test_require_healthy_policy_fails_when_current_audit_is_unhealthy() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=False),
        regression_service=StubRegressionService(regression_detected=False),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REQUIRE_HEALTHY
    )

    assert result.status is GovernanceIntegrityCheckStatus.UNHEALTHY
    assert result.passed is False


def test_require_healthy_policy_passes_when_current_audit_is_healthy() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=True),
        regression_service=StubRegressionService(regression_detected=False),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REQUIRE_HEALTHY
    )

    assert result.status is GovernanceIntegrityCheckStatus.PASSED
    assert result.passed is True


def test_regression_takes_priority_over_require_healthy_status() -> None:
    # Even under REQUIRE_HEALTHY, a detected regression should be reported
    # as REGRESSION_DETECTED (the more specific, actionable status) rather
    # than the generic UNHEALTHY.
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=False),
        regression_service=StubRegressionService(regression_detected=True),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REQUIRE_HEALTHY
    )

    assert (
        result.status
        is GovernanceIntegrityCheckStatus.REGRESSION_DETECTED
    )

    assert result.passed is False


def test_check_rejects_non_positive_batch_size() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=True),
        regression_service=StubRegressionService(regression_detected=False),
    )

    with pytest.raises(
        ValueError, match="batch_size must be greater than zero"
    ):
        service.check(batch_size=0)


def test_check_passes_batch_size_to_recording_service() -> None:
    recording_service = StubRecordingService(healthy=True)

    service = GovernanceIntegrityCheckService(
        recording_service=recording_service,
        regression_service=StubRegressionService(regression_detected=False),
    )

    service.check(batch_size=42)

    assert recording_service.batch_sizes == [42]


def test_check_records_current_audit_before_detecting_regression(
    tmp_path,
) -> None:
    database_path = tmp_path / "check-ordering.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    # Establish a healthy historical baseline.
    first_result = (
        runtime
        .build_integrity_audit_recording_service()
        .audit_and_record()
    )

    assert first_result.healthy is True

    result = runtime.build_integrity_check_service().check()

    assert result.regression.baseline_audit_id == first_result.audit_id
    assert result.regression.current_audit_id == result.audit_id
    assert runtime.audit_history_repository.count() == 2


def test_first_unhealthy_audit_passes_regression_only_policy() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=False),
        regression_service=StubRegressionService(
            snapshot=INSUFFICIENT_BASELINE_SNAPSHOT
        ),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REGRESSION_ONLY
    )

    assert result.status is GovernanceIntegrityCheckStatus.PASSED
    assert result.audit_healthy is False
    assert (
        result.regression.status
        is GovernanceIntegrityRegressionStatus.INSUFFICIENT_BASELINE
    )


def test_first_unhealthy_audit_fails_require_healthy_policy() -> None:
    service = GovernanceIntegrityCheckService(
        recording_service=StubRecordingService(healthy=False),
        regression_service=StubRegressionService(
            snapshot=INSUFFICIENT_BASELINE_SNAPSHOT
        ),
    )

    result = service.check(
        policy=GovernanceIntegrityCheckPolicy.REQUIRE_HEALTHY
    )

    assert result.status is GovernanceIntegrityCheckStatus.UNHEALTHY
    assert result.passed is False
