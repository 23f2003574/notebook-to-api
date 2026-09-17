from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from .deployment_governance_execution_metrics import (
    GovernanceIntegrityExecutionMetrics,
    GovernanceIntegrityExecutionMetricsService,
)


class GovernanceIntegrityAlertSeverity(
    str,
    Enum,
):
    """
    How urgently a generated execution alert should be treated.
    """

    INFO = "info"

    WARNING = "warning"

    CRITICAL = "critical"


@dataclass(frozen=True)
class GovernanceIntegrityExecutionAlert:
    """
    One generated alert: a threshold violation observed in execution
    metrics at a point in time.
    """

    alert_id: str

    severity: GovernanceIntegrityAlertSeverity

    message: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.alert_id.strip():
            raise ValueError(
                "alert_id must not be empty"
            )

        if not self.message.strip():
            raise ValueError(
                "message must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity.value,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class GovernanceIntegrityAlertPolicy:
    """
    Configured thresholds that generated execution metrics are
    checked against.

    Success and failure rates are expressed as percentages (0-100),
    not fractions, matching the CLI's --min-success/--max-failure
    options.
    """

    minimum_success_rate: float

    maximum_failure_rate: float

    maximum_average_duration_ms: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.minimum_success_rate <= 100.0):
            raise ValueError(
                "minimum_success_rate must be between 0 and 100"
            )

        if not (0.0 <= self.maximum_failure_rate <= 100.0):
            raise ValueError(
                "maximum_failure_rate must be between 0 and 100"
            )

        if self.maximum_average_duration_ms <= 0:
            raise ValueError(
                "maximum_average_duration_ms must be greater than zero"
            )


class GovernanceIntegrityExecutionAlertService:
    """
    Generates alerts when governance audit execution metrics cross
    configured thresholds.

    Alerts are generated, not persisted or dispatched: this service
    only reports violations for the caller to act on.
    """

    def __init__(
        self,
        metrics_service: GovernanceIntegrityExecutionMetricsService,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._metrics_service = metrics_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._uuid_factory = uuid_factory or (
            lambda: str(uuid.uuid4())
        )

    def generate(
        self,
        policy: GovernanceIntegrityAlertPolicy,
        *,
        template_name: str | None = None,
    ) -> tuple[
        GovernanceIntegrityExecutionAlert,
        ...
    ]:
        """
        Compute current execution metrics (overall, or for one
        template if template_name is given) and generate an alert for
        every threshold the metrics violate.

        Returns an empty tuple when nothing is violated.
        """

        metrics = (
            self._metrics_service.compute()
            if template_name is None
            else self._metrics_service.compute_for_template(
                template_name
            )
        )

        alerts: list[GovernanceIntegrityExecutionAlert] = []

        success_rate_percent = metrics.success_rate * 100.0

        if success_rate_percent < policy.minimum_success_rate:
            alerts.append(
                self._make_alert(
                    GovernanceIntegrityAlertSeverity.WARNING,
                    "success rate "
                    f"{success_rate_percent:.2f}% is below the "
                    f"minimum of {policy.minimum_success_rate:.2f}%",
                )
            )

        failure_rate_percent = self._failure_rate_percent(metrics)

        if failure_rate_percent > policy.maximum_failure_rate:
            alerts.append(
                self._make_alert(
                    GovernanceIntegrityAlertSeverity.WARNING,
                    "failure rate "
                    f"{failure_rate_percent:.2f}% exceeds the "
                    f"maximum of {policy.maximum_failure_rate:.2f}%",
                )
            )

        if (
            metrics.average_duration_ms
            > policy.maximum_average_duration_ms
        ):
            alerts.append(
                self._make_alert(
                    GovernanceIntegrityAlertSeverity.WARNING,
                    "average runtime "
                    f"{metrics.average_duration_ms:.0f}ms exceeds "
                    "the maximum of "
                    f"{policy.maximum_average_duration_ms:.0f}ms",
                )
            )

        return tuple(alerts)

    def _make_alert(
        self,
        severity: GovernanceIntegrityAlertSeverity,
        message: str,
    ) -> GovernanceIntegrityExecutionAlert:
        return GovernanceIntegrityExecutionAlert(
            alert_id=self._uuid_factory(),
            severity=severity,
            message=message,
            created_at=self._clock(),
        )

    @staticmethod
    def _failure_rate_percent(
        metrics: GovernanceIntegrityExecutionMetrics,
    ) -> float:
        if metrics.total_runs == 0:
            return 0.0

        return (
            metrics.failed_runs / metrics.total_runs
        ) * 100.0
