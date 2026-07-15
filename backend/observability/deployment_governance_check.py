from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .deployment_governance_audit_recording import (
    GovernanceIntegrityAuditRecordingService,
)
from .deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionService,
    GovernanceIntegrityRegressionSnapshot,
)


class GovernanceIntegrityCheckPolicy(
    str,
    Enum,
):
    """
    Policy controlling when an integrity check fails.
    """

    REGRESSION_ONLY = "regression_only"

    REQUIRE_HEALTHY = "require_healthy"


class GovernanceIntegrityCheckStatus(
    str,
    Enum,
):
    """
    Final status of one governance integrity check.
    """

    PASSED = "passed"

    REGRESSION_DETECTED = "regression_detected"

    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class GovernanceIntegrityCheckResult:
    """
    Result of executing, recording, and evaluating one integrity audit.
    """

    status: GovernanceIntegrityCheckStatus

    policy: GovernanceIntegrityCheckPolicy

    passed: bool

    audit_id: str

    audit_healthy: bool

    regression: GovernanceIntegrityRegressionSnapshot

    def __post_init__(self) -> None:
        expected_passed = (
            self.status is GovernanceIntegrityCheckStatus.PASSED
        )

        if self.passed != expected_passed:
            raise ValueError(
                "passed must match the check status"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "policy": self.policy.value,
            "passed": self.passed,
            "audit_id": self.audit_id,
            "audit_healthy": self.audit_healthy,
            "regression": self.regression.to_dict(),
        }


class GovernanceIntegrityCheckService:
    """
    Executes a fresh integrity audit and evaluates governance policy.

    This is the CI-oriented automation primitive: unlike
    GovernanceIntegrityRegressionService.detect() (passive inspection), a
    call to check() always executes and persists a brand-new audit before
    evaluating policy, so the result reflects the current state of the
    store rather than whatever was last recorded.
    """

    def __init__(
        self,
        *,
        recording_service: GovernanceIntegrityAuditRecordingService,
        regression_service: GovernanceIntegrityRegressionService,
    ) -> None:
        self._recording_service = recording_service
        self._regression_service = regression_service

    def check(
        self,
        *,
        policy: GovernanceIntegrityCheckPolicy = (
            GovernanceIntegrityCheckPolicy.REGRESSION_ONLY
        ),
        batch_size: int = 500,
    ) -> GovernanceIntegrityCheckResult:
        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        # Order matters: the fresh audit must be recorded before regression
        # detection runs, or the regression service would compare stale
        # historical records and miss the audit this check just executed.
        recording_result = self._recording_service.audit_and_record(
            batch_size=batch_size
        )

        regression = self._regression_service.detect()

        audit_healthy = recording_result.report.healthy

        status = self._evaluate_status(
            policy=policy,
            audit_healthy=audit_healthy,
            regression=regression,
        )

        return GovernanceIntegrityCheckResult(
            status=status,
            policy=policy,
            passed=status is GovernanceIntegrityCheckStatus.PASSED,
            audit_id=recording_result.audit_id,
            audit_healthy=audit_healthy,
            regression=regression,
        )

    @staticmethod
    def _evaluate_status(
        *,
        policy: GovernanceIntegrityCheckPolicy,
        audit_healthy: bool,
        regression: GovernanceIntegrityRegressionSnapshot,
    ) -> GovernanceIntegrityCheckStatus:
        if regression.regression_detected:
            return GovernanceIntegrityCheckStatus.REGRESSION_DETECTED

        if (
            policy is GovernanceIntegrityCheckPolicy.REQUIRE_HEALTHY
            and not audit_healthy
        ):
            return GovernanceIntegrityCheckStatus.UNHEALTHY

        return GovernanceIntegrityCheckStatus.PASSED
