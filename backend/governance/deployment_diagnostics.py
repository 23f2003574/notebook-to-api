from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .deployment_alerts import DeploymentAlertManager, get_deployment_alert_manager
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
    get_deployment_recovery_coordinator,
)
from .deployment_tracing import DeploymentTracingService, get_deployment_tracing_service

_LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
_ALERT_LEVEL_ORDER = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}


class UnknownDeploymentError(KeyError):
    pass


@dataclass(frozen=True)
class DiagnosticSnapshot:
    """An immutable point-in-time capture of everything known about a deployment."""

    deployment: str
    captured_at: datetime
    logs: tuple = ()
    traces: tuple = ()
    metrics: Optional[dict] = None
    alerts: tuple = ()
    recovery_events: tuple = ()

    def to_dict(self) -> dict:
        return {
            "deployment": self.deployment,
            "captured_at": self.captured_at.isoformat(),
            "logs": list(self.logs),
            "traces": list(self.traces),
            "metrics": self.metrics,
            "alerts": list(self.alerts),
            "recovery_events": list(self.recovery_events),
        }


def _build_timeline(snapshot: DiagnosticSnapshot) -> list[dict]:
    events = []
    for log in snapshot.logs:
        events.append(
            {
                "timestamp": log["timestamp"],
                "source": "log",
                "level": log["level"],
                "summary": log["message"],
            }
        )
    for alert in snapshot.alerts:
        events.append(
            {
                "timestamp": alert["triggered_at"],
                "source": "alert",
                "level": alert["level"],
                "summary": alert["message"],
            }
        )
    for recovery in snapshot.recovery_events:
        events.append(
            {
                "timestamp": recovery["started_at"],
                "source": "recovery",
                "level": recovery["status"],
                "summary": f"{recovery['strategy']}: {recovery['message']}",
            }
        )
    events.sort(key=lambda event: event["timestamp"])
    return events


def _summarize_root_cause(snapshot: DiagnosticSnapshot) -> str:
    if snapshot.alerts:
        worst = max(
            snapshot.alerts,
            key=lambda alert: _ALERT_LEVEL_ORDER.get(alert["level"], 0),
        )
        return f"alert '{worst['rule_name']}' ({worst['level']}): {worst['message']}"

    error_logs = [
        log
        for log in snapshot.logs
        if _LOG_LEVEL_ORDER.get(log["level"], 0) >= _LOG_LEVEL_ORDER["ERROR"]
    ]
    if error_logs:
        earliest = min(error_logs, key=lambda log: log["timestamp"])
        return f"log entry ({earliest['level']}): {earliest['message']}"

    failed_recoveries = [
        recovery for recovery in snapshot.recovery_events if recovery["status"] == "FAILED"
    ]
    if failed_recoveries:
        latest = failed_recoveries[-1]
        return f"recovery strategy '{latest['strategy']}' failed: {latest['message']}"

    return "no significant issues detected"


@dataclass(frozen=True)
class DiagnosticReport:
    """One immutable diagnostics report for a deployment."""

    deployment: str
    generated_at: datetime
    timeline: tuple = field(default_factory=tuple)
    root_cause: str = "no significant issues detected"
    snapshot: Optional[DiagnosticSnapshot] = None

    def to_dict(self) -> dict:
        return {
            "deployment": self.deployment,
            "generated_at": self.generated_at.isoformat(),
            "timeline": list(self.timeline),
            "root_cause": self.root_cause,
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }


class DeploymentDiagnosticsService:
    """Collects and queries diagnostic information across governance services."""

    def __init__(self) -> None:
        self._snapshots: dict[str, DiagnosticSnapshot] = {}
        self._latest_report: dict[str, DiagnosticReport] = {}
        self._history: list[DiagnosticReport] = []
        self._lock = Lock()

    def capture(
        self,
        deployment: str,
        *,
        logging_service: Optional[DeploymentLoggingService] = None,
        tracing_service: Optional[DeploymentTracingService] = None,
        metrics_collector: Optional[DeploymentMetricsCollector] = None,
        alert_manager: Optional[DeploymentAlertManager] = None,
        recovery_coordinator: Optional[DeploymentRecoveryCoordinator] = None,
        timestamp: Optional[datetime] = None,
    ) -> DiagnosticSnapshot:
        if not deployment:
            raise ValueError("deployment identifier is required")

        logs: list[dict] = []
        if logging_service is not None:
            logs = [entry.to_dict() for entry in logging_service.query(deployment=deployment)]

        traces: list[dict] = []
        if tracing_service is not None and logging_service is not None:
            for trace_id in logging_service.trace_ids(deployment):
                try:
                    spans = tracing_service.get_trace(trace_id)
                except KeyError:
                    continue
                traces.extend(span.to_dict() for span in spans)

        metrics = None
        if metrics_collector is not None:
            metrics = metrics_collector.snapshot().to_dict()

        alerts: list[dict] = []
        if alert_manager is not None:
            alerts = [
                alert.to_dict()
                for alert in alert_manager.active_alerts()
                if alert.rule_name == deployment
            ]

        recovery_events: list[dict] = []
        if recovery_coordinator is not None:
            recovery_events = [
                record.to_dict()
                for record in recovery_coordinator.history(deployment=deployment)
            ]

        snapshot = DiagnosticSnapshot(
            deployment=deployment,
            captured_at=timestamp or datetime.now(timezone.utc),
            logs=tuple(logs),
            traces=tuple(traces),
            metrics=metrics,
            alerts=tuple(alerts),
            recovery_events=tuple(recovery_events),
        )
        with self._lock:
            self._snapshots[deployment] = snapshot
        return snapshot

    def analyze(
        self,
        deployment: str,
        *,
        snapshot: Optional[DiagnosticSnapshot] = None,
        timestamp: Optional[datetime] = None,
        **capture_kwargs,
    ) -> DiagnosticReport:
        if snapshot is None:
            with self._lock:
                snapshot = self._snapshots.get(deployment)
            if snapshot is None:
                snapshot = self.capture(deployment, timestamp=timestamp, **capture_kwargs)

        report = DiagnosticReport(
            deployment=deployment,
            generated_at=timestamp or datetime.now(timezone.utc),
            timeline=tuple(_build_timeline(snapshot)),
            root_cause=_summarize_root_cause(snapshot),
            snapshot=snapshot,
        )
        with self._lock:
            self._history.append(report)
            self._latest_report[deployment] = report
        return report

    def report(self, deployment: str) -> DiagnosticReport:
        with self._lock:
            report = self._latest_report.get(deployment)
        if report is None:
            raise UnknownDeploymentError(deployment)
        return report

    def history(self, deployment: Optional[str] = None) -> list[DiagnosticReport]:
        with self._lock:
            reports = list(self._history)
        if deployment is not None:
            reports = [r for r in reports if r.deployment == deployment]
        return reports


_service = DeploymentDiagnosticsService()


def get_deployment_diagnostics_service() -> DeploymentDiagnosticsService:
    return _service


router = APIRouter(prefix="/governance", tags=["governance-diagnostics"])


@router.post("/diagnostics/analyze")
def analyze_deployment(payload: dict = Body(...)) -> dict:
    deployment = payload.get("deployment")
    if not deployment:
        raise HTTPException(status_code=422, detail="deployment is required")
    report = get_deployment_diagnostics_service().analyze(
        deployment,
        logging_service=get_deployment_logging_service(),
        tracing_service=get_deployment_tracing_service(),
        metrics_collector=get_deployment_metrics_collector(),
        alert_manager=get_deployment_alert_manager(),
        recovery_coordinator=get_deployment_recovery_coordinator(),
    )
    return report.to_dict()


@router.get("/diagnostics/history")
def diagnostics_history(deployment: Optional[str] = Query(default=None)) -> list[dict]:
    reports = get_deployment_diagnostics_service().history(deployment=deployment)
    return [report.to_dict() for report in reports]


@router.get("/diagnostics/{deployment}")
def get_diagnostics_report(deployment: str) -> dict:
    try:
        report = get_deployment_diagnostics_service().report(deployment)
    except UnknownDeploymentError:
        raise HTTPException(status_code=404, detail="no diagnostics for deployment")
    return report.to_dict()
