from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Query

from .deployment_alerts import DeploymentAlertManager, get_deployment_alert_manager
from .deployment_capacity import (
    DeploymentCapacityMonitor,
    get_deployment_capacity_monitor,
)
from .deployment_diagnostics import (
    DeploymentDiagnosticsService,
    UnknownDeploymentError as UnknownDiagnosticsDeploymentError,
    get_deployment_diagnostics_service,
)
from .deployment_insights import (
    DeploymentInsightsService,
    get_deployment_insights_service,
)
from .deployment_logging import (
    DeploymentLoggingService,
    get_deployment_logging_service,
)
from .deployment_metrics import (
    DeploymentMetricsCollector,
    get_deployment_metrics_collector,
)
from .deployment_recovery import (
    DeploymentRecoveryCoordinator,
    UnknownDeploymentError as UnknownRecoveryDeploymentError,
    get_deployment_recovery_coordinator,
)
from .deployment_slo import DeploymentSLOManager, get_deployment_slo_manager
from .deployment_tracing import DeploymentTracingService, get_deployment_tracing_service


@dataclass(frozen=True)
class DashboardSnapshot:
    """One immutable, cached view of the system-wide observability state."""

    generated_at: datetime
    sections: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "sections": dict(self.sections),
        }


class DeploymentObservabilityDashboard:
    """
    Read-only aggregation of existing governance services into a single
    dashboard. Introduces no new business logic of its own.
    """

    def __init__(
        self,
        *,
        logging_service: Optional[DeploymentLoggingService] = None,
        tracing_service: Optional[DeploymentTracingService] = None,
        metrics_collector: Optional[DeploymentMetricsCollector] = None,
        alert_manager: Optional[DeploymentAlertManager] = None,
        slo_manager: Optional[DeploymentSLOManager] = None,
        capacity_monitor: Optional[DeploymentCapacityMonitor] = None,
        recovery_coordinator: Optional[DeploymentRecoveryCoordinator] = None,
        diagnostics_service: Optional[DeploymentDiagnosticsService] = None,
        insights_service: Optional[DeploymentInsightsService] = None,
    ) -> None:
        self._logging_service = logging_service
        self._tracing_service = tracing_service
        self._metrics_collector = metrics_collector
        self._alert_manager = alert_manager
        self._slo_manager = slo_manager
        self._capacity_monitor = capacity_monitor
        self._recovery_coordinator = recovery_coordinator
        self._diagnostics_service = diagnostics_service
        self._insights_service = insights_service
        self._cached_overview: Optional[DashboardSnapshot] = None
        self._lock = Lock()

    def overview(self, *, timestamp: Optional[datetime] = None) -> DashboardSnapshot:
        with self._lock:
            cached = self._cached_overview
        if cached is not None:
            return cached
        return self.refresh(timestamp=timestamp)

    def refresh(self, *, timestamp: Optional[datetime] = None) -> DashboardSnapshot:
        now = timestamp or datetime.now(timezone.utc)

        metrics = (
            self._metrics_collector.snapshot().to_dict()
            if self._metrics_collector is not None
            else None
        )
        alerts = (
            [alert.to_dict() for alert in self._alert_manager.active_alerts()]
            if self._alert_manager is not None
            else []
        )
        slo = (
            [result.to_dict() for result in self._slo_manager.status()]
            if self._slo_manager is not None
            else []
        )
        capacity = (
            {
                name: measurement.to_dict()
                for name, measurement in self._capacity_monitor.utilization().items()
            }
            if self._capacity_monitor is not None
            else {}
        )
        insights = (
            {
                "summary": self._insights_service.summary(),
                "latest": [i.to_dict() for i in self._insights_service.latest()],
            }
            if self._insights_service is not None
            else {}
        )

        snapshot = DashboardSnapshot(
            generated_at=now,
            sections={
                "metrics": metrics,
                "alerts": alerts,
                "slo": slo,
                "capacity": capacity,
                "insights": insights,
            },
        )
        with self._lock:
            self._cached_overview = snapshot
        return snapshot

    def deployment(
        self, deployment_id: str, *, timestamp: Optional[datetime] = None
    ) -> dict:
        if not deployment_id:
            raise ValueError("deployment identifier is required")

        diagnostics = None
        if self._diagnostics_service is not None:
            try:
                diagnostics = self._diagnostics_service.report(deployment_id).to_dict()
            except UnknownDiagnosticsDeploymentError:
                diagnostics = self._diagnostics_service.analyze(
                    deployment_id,
                    logging_service=self._logging_service,
                    tracing_service=self._tracing_service,
                    metrics_collector=self._metrics_collector,
                    alert_manager=self._alert_manager,
                    recovery_coordinator=self._recovery_coordinator,
                    insights_service=self._insights_service,
                    timestamp=timestamp,
                ).to_dict()

        alerts = (
            [
                alert.to_dict()
                for alert in self._alert_manager.active_alerts()
                if alert.rule_name == deployment_id
            ]
            if self._alert_manager is not None
            else []
        )

        recovery = None
        if self._recovery_coordinator is not None:
            try:
                recovery = self._recovery_coordinator.status(deployment_id).to_dict()
            except UnknownRecoveryDeploymentError:
                recovery = None

        insights = (
            self._insights_service.summary(deployment_id)
            if self._insights_service is not None
            else {}
        )

        return {
            "deployment": deployment_id,
            "generated_at": (timestamp or datetime.now(timezone.utc)).isoformat(),
            "sections": {
                "diagnostics": diagnostics,
                "alerts": alerts,
                "recovery": recovery,
                "insights": insights,
            },
        }

    def health(self) -> dict:
        alerts = (
            self._alert_manager.active_alerts()
            if self._alert_manager is not None
            else []
        )
        slo_results = self._slo_manager.status() if self._slo_manager is not None else []
        capacity = (
            self._capacity_monitor.utilization()
            if self._capacity_monitor is not None
            else {}
        )

        status = "HEALTHY"
        if (
            any(alert.level == "CRITICAL" for alert in alerts)
            or any(result.status == "BREACHED" for result in slo_results)
            or any(m.status == "CRITICAL" for m in capacity.values())
        ):
            status = "CRITICAL"
        elif (
            alerts
            or any(result.status == "AT_RISK" for result in slo_results)
            or any(m.status == "WARNING" for m in capacity.values())
        ):
            status = "WARNING"

        return {
            "status": status,
            "active_alerts": len(alerts),
            "slo_breaches": sum(1 for r in slo_results if r.status != "HEALTHY"),
            "capacity_warnings": sum(1 for m in capacity.values() if m.status != "OK"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


def _build_default_dashboard() -> DeploymentObservabilityDashboard:
    return DeploymentObservabilityDashboard(
        logging_service=get_deployment_logging_service(),
        tracing_service=get_deployment_tracing_service(),
        metrics_collector=get_deployment_metrics_collector(),
        alert_manager=get_deployment_alert_manager(),
        slo_manager=get_deployment_slo_manager(),
        capacity_monitor=get_deployment_capacity_monitor(),
        recovery_coordinator=get_deployment_recovery_coordinator(),
        diagnostics_service=get_deployment_diagnostics_service(),
        insights_service=get_deployment_insights_service(),
    )


_dashboard = _build_default_dashboard()


def get_deployment_observability_dashboard() -> DeploymentObservabilityDashboard:
    return _dashboard


router = APIRouter(prefix="/governance", tags=["governance-observability"])


@router.get("/observability")
def observability_overview(refresh: bool = Query(default=False)) -> dict:
    dashboard = get_deployment_observability_dashboard()
    snapshot = dashboard.refresh() if refresh else dashboard.overview()
    return snapshot.to_dict()


@router.get("/observability/health")
def observability_health() -> dict:
    return get_deployment_observability_dashboard().health()


@router.get("/observability/deployments/{deployment_id}")
def observability_deployment(deployment_id: str) -> dict:
    return get_deployment_observability_dashboard().deployment(deployment_id)
