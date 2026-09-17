from __future__ import annotations

from typing import TYPE_CHECKING

from .deployment_governance_metrics import GovernanceIntegrityMetrics
from .deployment_governance_metrics_dashboard import (
    GovernanceIntegrityMetricsDashboard,
    GovernanceIntegrityMetricsDashboardService,
)

if TYPE_CHECKING:
    from .deployment_governance_metrics import (
        GovernanceIntegrityMetricsService,
    )
    from .deployment_governance_metrics_alerts import (
        GovernanceIntegrityMetricAlert,
        GovernanceIntegrityMetricsAlertService,
    )
    from .deployment_governance_metrics_history import (
        GovernanceIntegrityMetricsSnapshot,
    )


class GovernanceIntegrityMetricsApi:
    """
    Lightweight, read-only facade over governance audit notification
    delivery metrics, intended for future frontend integration.

    Every method returns an already-built DTO (GovernanceIntegrity
    Metrics, GovernanceIntegrityMetricsSnapshot,
    GovernanceIntegrityMetricAlert, GovernanceIntegrityMetrics
    Dashboard) taken from the services it wraps. It never records,
    flushes, resets, or otherwise mutates state itself: those
    operations belong to the underlying services, not this facade.
    """

    def __init__(
        self,
        metrics_service: "GovernanceIntegrityMetricsService",
        *,
        alert_service: (
            "GovernanceIntegrityMetricsAlertService | None"
        ) = None,
    ) -> None:
        self._metrics_service = metrics_service

        self._alert_service = alert_service

        self._dashboard_service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, alert_service=alert_service
        )

    def summary(self) -> GovernanceIntegrityMetrics:
        """
        Return the current governance audit notification delivery
        metrics counters.
        """

        return self._metrics_service.snapshot()

    def history(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple["GovernanceIntegrityMetricsSnapshot", ...]:
        """
        Return captured metrics snapshots, newest first, with
        limit/offset pagination applied on top of that deterministic
        ordering.
        """

        if offset < 0:
            raise ValueError(
                "offset must not be negative"
            )

        if limit is not None and limit < 0:
            raise ValueError(
                "limit must not be negative"
            )

        snapshots = self._metrics_service.history()

        if limit is None:
            return snapshots[offset:]

        return snapshots[offset:offset + limit]

    def alerts(self) -> tuple["GovernanceIntegrityMetricAlert", ...]:
        """
        Return every currently active (triggered) metric alert, or
        an empty tuple if no alert service is configured.
        """

        if self._alert_service is None:
            return ()

        return self._alert_service.active()

    def dashboard(self) -> GovernanceIntegrityMetricsDashboard:
        """
        Return a compact dashboard DTO built from the current
        in-memory metrics and alert state.
        """

        return self._dashboard_service.overview()
