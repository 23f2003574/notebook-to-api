from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)


class GovernanceIntegrityRegressionStatus(
    str,
    Enum,
):
    """
    Result of comparing the latest integrity audit with historical state.
    """

    NO_HISTORY = "no_history"

    INSUFFICIENT_BASELINE = "insufficient_baseline"

    HEALTHY = "healthy"

    PERSISTENT_FAILURE = "persistent_failure"

    REGRESSION = "regression"


_FAILURE_CATEGORY_FIELDS = (
    ("integrity_mismatches", "integrity_mismatches"),
    ("missing_integrity_metadata", "missing_integrity_metadata"),
    ("invalid_integrity_metadata", "invalid_integrity_metadata"),
    ("invalid_persisted_records", "invalid_persisted_records"),
)


def determine_newly_introduced_failure_categories(
    *,
    baseline: GovernanceIntegrityAuditRecord,
    current: GovernanceIntegrityAuditRecord,
) -> tuple[str, ...]:
    """
    Return failure categories absent in the baseline but present currently.
    """

    categories: list[str] = []

    for field_name, category_name in _FAILURE_CATEGORY_FIELDS:
        baseline_value = getattr(baseline, field_name)
        current_value = getattr(current, field_name)

        if baseline_value == 0 and current_value > 0:
            categories.append(category_name)

    return tuple(categories)


@dataclass(frozen=True)
class GovernanceIntegrityRegressionSnapshot:
    """
    Derived regression state for the latest governance integrity audit.

    Compares only the latest audit against the immediately preceding one
    (not the nearest historical healthy audit), so a run of consecutive
    unhealthy audits is reported as one REGRESSION followed by
    PERSISTENT_FAILURE entries rather than a regression on every audit.
    """

    status: GovernanceIntegrityRegressionStatus

    regression_detected: bool

    current_audit_id: str | None

    baseline_audit_id: str | None

    current_outcome: GovernanceIntegrityAuditOutcome | None

    baseline_outcome: GovernanceIntegrityAuditOutcome | None

    current_invalid_records: int | None

    baseline_invalid_records: int | None

    invalid_record_delta: int | None

    integrity_mismatch_delta: int | None

    missing_integrity_metadata_delta: int | None

    invalid_integrity_metadata_delta: int | None

    invalid_persisted_records_delta: int | None

    newly_introduced_failure_categories: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.regression_detected != (
            self.status is GovernanceIntegrityRegressionStatus.REGRESSION
        ):
            raise ValueError(
                "regression_detected must match regression status"
            )

        if (
            self.status is GovernanceIntegrityRegressionStatus.NO_HISTORY
            and self.current_audit_id is not None
        ):
            raise ValueError(
                "current_audit_id must be absent when history is empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "regression_detected": self.regression_detected,
            "current_audit_id": self.current_audit_id,
            "baseline_audit_id": self.baseline_audit_id,
            "current_outcome": (
                None
                if self.current_outcome is None
                else self.current_outcome.value
            ),
            "baseline_outcome": (
                None
                if self.baseline_outcome is None
                else self.baseline_outcome.value
            ),
            "current_invalid_records": self.current_invalid_records,
            "baseline_invalid_records": self.baseline_invalid_records,
            "invalid_record_delta": self.invalid_record_delta,
            "integrity_mismatch_delta": self.integrity_mismatch_delta,
            "missing_integrity_metadata_delta": (
                self.missing_integrity_metadata_delta
            ),
            "invalid_integrity_metadata_delta": (
                self.invalid_integrity_metadata_delta
            ),
            "invalid_persisted_records_delta": (
                self.invalid_persisted_records_delta
            ),
            "newly_introduced_failure_categories": list(
                self.newly_introduced_failure_categories
            ),
        }


def detect_governance_integrity_regression(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
) -> GovernanceIntegrityRegressionSnapshot:
    """
    Pure regression detection over an already-selected, newest-first
    record set (only the first two entries matter).

    Extracted from GovernanceIntegrityRegressionService.detect() so other
    callers (e.g. the evidence export service) can derive a regression
    snapshot from a specific record subset without re-querying the
    repository, keeping a bundle's regression analysis self-consistent
    with the records it contains.
    """

    if not records:
        return _no_history_snapshot()

    current = records[0]

    if len(records) == 1:
        return _single_audit_snapshot(current)

    baseline = records[1]

    return _compare_regression(baseline=baseline, current=current)


def _no_history_snapshot() -> GovernanceIntegrityRegressionSnapshot:
    return GovernanceIntegrityRegressionSnapshot(
        status=GovernanceIntegrityRegressionStatus.NO_HISTORY,
        regression_detected=False,
        current_audit_id=None,
        baseline_audit_id=None,
        current_outcome=None,
        baseline_outcome=None,
        current_invalid_records=None,
        baseline_invalid_records=None,
        invalid_record_delta=None,
        integrity_mismatch_delta=None,
        missing_integrity_metadata_delta=None,
        invalid_integrity_metadata_delta=None,
        invalid_persisted_records_delta=None,
        newly_introduced_failure_categories=(),
    )


def _single_audit_snapshot(
    current: GovernanceIntegrityAuditRecord,
) -> GovernanceIntegrityRegressionSnapshot:
    status = (
        GovernanceIntegrityRegressionStatus.HEALTHY
        if current.healthy
        else GovernanceIntegrityRegressionStatus.INSUFFICIENT_BASELINE
    )

    return GovernanceIntegrityRegressionSnapshot(
        status=status,
        regression_detected=False,
        current_audit_id=current.audit_id,
        baseline_audit_id=None,
        current_outcome=current.outcome,
        baseline_outcome=None,
        current_invalid_records=current.invalid_records,
        baseline_invalid_records=None,
        invalid_record_delta=None,
        integrity_mismatch_delta=None,
        missing_integrity_metadata_delta=None,
        invalid_integrity_metadata_delta=None,
        invalid_persisted_records_delta=None,
        newly_introduced_failure_categories=(),
    )


def _compare_regression(
    *,
    baseline: GovernanceIntegrityAuditRecord,
    current: GovernanceIntegrityAuditRecord,
) -> GovernanceIntegrityRegressionSnapshot:
    if current.healthy:
        status = GovernanceIntegrityRegressionStatus.HEALTHY

    elif baseline.healthy:
        status = GovernanceIntegrityRegressionStatus.REGRESSION

    else:
        status = GovernanceIntegrityRegressionStatus.PERSISTENT_FAILURE

    return GovernanceIntegrityRegressionSnapshot(
        status=status,
        regression_detected=(
            status is GovernanceIntegrityRegressionStatus.REGRESSION
        ),
        current_audit_id=current.audit_id,
        baseline_audit_id=baseline.audit_id,
        current_outcome=current.outcome,
        baseline_outcome=baseline.outcome,
        current_invalid_records=current.invalid_records,
        baseline_invalid_records=baseline.invalid_records,
        invalid_record_delta=(
            current.invalid_records - baseline.invalid_records
        ),
        integrity_mismatch_delta=(
            current.integrity_mismatches
            - baseline.integrity_mismatches
        ),
        missing_integrity_metadata_delta=(
            current.missing_integrity_metadata
            - baseline.missing_integrity_metadata
        ),
        invalid_integrity_metadata_delta=(
            current.invalid_integrity_metadata
            - baseline.invalid_integrity_metadata
        ),
        invalid_persisted_records_delta=(
            current.invalid_persisted_records
            - baseline.invalid_persisted_records
        ),
        newly_introduced_failure_categories=(
            determine_newly_introduced_failure_categories(
                baseline=baseline,
                current=current,
            )
        ),
    )


class GovernanceIntegrityRegressionService:
    """
    Detects newly introduced integrity regressions from audit history.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityAuditHistoryRepository,
    ) -> None:
        self._repository = repository

    def detect(self) -> GovernanceIntegrityRegressionSnapshot:
        """
        Compare the latest audit with the immediately preceding audit.
        """

        records = self._repository.list(limit=2)

        return detect_governance_integrity_regression(records)
