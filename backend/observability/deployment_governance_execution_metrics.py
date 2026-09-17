from __future__ import annotations

from dataclasses import dataclass

from .deployment_governance_audit_worker import (
    GovernanceIntegrityAuditExecutionRecord,
    GovernanceIntegrityAuditExecutionRepository,
    GovernanceIntegrityExecutionResult,
)


@dataclass(frozen=True)
class GovernanceIntegrityExecutionMetrics:
    """
    Aggregate operational metrics derived from governance audit
    worker execution history.
    """

    total_runs: int

    successful_runs: int

    failed_runs: int

    average_duration_ms: float

    success_rate: float

    def __post_init__(self) -> None:
        non_negative_fields = (
            self.total_runs,
            self.successful_runs,
            self.failed_runs,
        )

        if any(value < 0 for value in non_negative_fields):
            raise ValueError(
                "execution metrics counts must not be negative"
            )

        if (
            self.successful_runs + self.failed_runs
            != self.total_runs
        ):
            raise ValueError(
                "successful_runs + failed_runs must equal total_runs"
            )

        if self.average_duration_ms < 0:
            raise ValueError(
                "average_duration_ms must not be negative"
            )

        if not (0.0 <= self.success_rate <= 1.0):
            raise ValueError(
                "success_rate must be between zero and one"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "average_duration_ms": self.average_duration_ms,
            "success_rate": self.success_rate,
        }


class GovernanceIntegrityExecutionMetricsService:
    """
    Computes aggregate execution metrics from governance audit worker
    execution history.
    """

    def __init__(
        self,
        execution_repository: GovernanceIntegrityAuditExecutionRepository,
    ) -> None:
        self._execution_repository = execution_repository

    def compute(
        self,
    ) -> GovernanceIntegrityExecutionMetrics:
        """
        Compute metrics across every stored execution record.
        """

        return self._compute_for_records(
            self._execution_repository.list()
        )

    def compute_for_template(
        self,
        template_name: str,
    ) -> GovernanceIntegrityExecutionMetrics:
        """
        Compute metrics across execution records for one template.
        """

        records = tuple(
            record
            for record in self._execution_repository.list()
            if record.template_name == template_name
        )

        return self._compute_for_records(records)

    @staticmethod
    def _compute_for_records(
        records: tuple[
            GovernanceIntegrityAuditExecutionRecord,
            ...
        ],
    ) -> GovernanceIntegrityExecutionMetrics:
        total_runs = len(records)

        if total_runs == 0:
            return GovernanceIntegrityExecutionMetrics(
                total_runs=0,
                successful_runs=0,
                failed_runs=0,
                average_duration_ms=0.0,
                success_rate=0.0,
            )

        successful_runs = sum(
            1
            for record in records
            if record.result is GovernanceIntegrityExecutionResult.SUCCESS
        )

        failed_runs = total_runs - successful_runs

        total_duration_ms = sum(
            (
                record.finished_at - record.started_at
            ).total_seconds()
            * 1000.0
            for record in records
        )

        return GovernanceIntegrityExecutionMetrics(
            total_runs=total_runs,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            average_duration_ms=total_duration_ms / total_runs,
            success_rate=successful_runs / total_runs,
        )
