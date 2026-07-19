from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from .deployment_governance_metrics import GovernanceIntegrityMetrics

if TYPE_CHECKING:
    from .deployment_governance_metrics import (
        GovernanceIntegrityMetricsService,
    )
    from .deployment_governance_metrics_alerts import (
        GovernanceIntegrityMetricsAlertService,
    )

_EMPTY_METRICS = GovernanceIntegrityMetrics(
    total_dispatches=0,
    successful_dispatches=0,
    failed_dispatches=0,
    retry_dispatches=0,
    average_duration_ms=0.0,
)


@dataclass(frozen=True)
class GovernanceIntegrityMetricsDashboard:
    """
    A compact, read-only view of governance audit notification
    delivery metrics, intended for future dashboards: the raw
    counters plus derived percentages and alert state, all as of one
    point in time.
    """

    summary: GovernanceIntegrityMetrics

    success_rate: float

    failure_rate: float

    retry_rate: float

    active_alerts: int

    last_updated: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("success_rate", self.success_rate),
            ("failure_rate", self.failure_rate),
            ("retry_rate", self.retry_rate),
        ):
            if not (0.0 <= value <= 100.0):
                raise ValueError(
                    f"{field_name} must be between 0 and 100"
                )

        if self.active_alerts < 0:
            raise ValueError(
                "active_alerts must not be negative"
            )

        if self.last_updated.tzinfo is None:
            raise ValueError(
                "last_updated must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary.to_dict(),
            "success_rate": self.success_rate,
            "failure_rate": self.failure_rate,
            "retry_rate": self.retry_rate,
            "active_alerts": self.active_alerts,
            "last_updated": self.last_updated.isoformat(),
        }


class GovernanceIntegrityMetricsDashboardService:
    """
    Builds read-only dashboard DTOs from live governance audit
    notification delivery metrics and, when configured, active
    alerts.

    This service never mutates the metrics or alert state it reads
    from (aside from refresh() explicitly resyncing them from their
    own sources); it only derives and formats a snapshot for
    display.
    """

    def __init__(
        self,
        metrics_service: "GovernanceIntegrityMetricsService",
        *,
        alert_service: (
            "GovernanceIntegrityMetricsAlertService | None"
        ) = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._metrics_service = metrics_service

        self._alert_service = alert_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def summary(self) -> GovernanceIntegrityMetrics:
        """
        Return the current raw metrics counters, unmodified.
        """

        return self._metrics_service.snapshot()

    def overview(self) -> GovernanceIntegrityMetricsDashboard:
        """
        Build a dashboard DTO from whatever metrics and alert state
        are currently held in memory, without resyncing from durable
        storage first. Use refresh() to resync before building.
        """

        metrics = self._metrics_service.snapshot()

        active_alerts = (
            0
            if self._alert_service is None
            else len(self._alert_service.active())
        )

        return GovernanceIntegrityMetricsDashboard(
            summary=metrics,
            success_rate=self._percentage(
                metrics.successful_dispatches,
                metrics.total_dispatches,
            ),
            failure_rate=self._percentage(
                metrics.failed_dispatches,
                metrics.total_dispatches,
            ),
            retry_rate=self._percentage(
                metrics.retry_dispatches,
                metrics.total_dispatches,
            ),
            active_alerts=active_alerts,
            last_updated=self._clock(),
        )

    def refresh(self) -> GovernanceIntegrityMetricsDashboard:
        """
        Resync metrics from durable storage and, if configured,
        re-evaluate alerts against the resynced metrics, then build
        a fresh dashboard DTO from the result.
        """

        self._metrics_service.load()

        if self._alert_service is not None:
            self._alert_service.evaluate(
                self._metrics_service.snapshot()
            )

        return self.overview()

    @staticmethod
    def _percentage(numerator: int, denominator: int) -> float:
        if denominator == 0:
            return 0.0

        return round((numerator / denominator) * 100.0, 2)
