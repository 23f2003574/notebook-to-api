from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .deployment_capacity import DeploymentCapacityMonitor
from .deployment_metrics import (
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    SUCCESS_COUNT,
    DeploymentMetricsCollector,
    MetricsSnapshot,
)
from .deployment_recovery import DeploymentRecoveryCoordinator
from .deployment_slo import DeploymentSLOManager

INSIGHT_CATEGORIES = (
    "RELIABILITY",
    "PERFORMANCE",
    "CAPACITY",
    "RECOVERY",
    "DEPLOYMENT_TRENDS",
)
INSIGHT_SEVERITIES = ("INFO", "WARNING", "CRITICAL")

_LATENCY_TREND_FACTOR = 1.2
_FAILURE_RATE_TREND_DELTA = 0.1
_LATENCY_ANOMALY_FACTOR = 3.0


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownDeploymentError(KeyError):
    pass


def _failure_rate(snapshot: MetricsSnapshot) -> Optional[float]:
    success = snapshot.counters.get(SUCCESS_COUNT, 0.0)
    failure = snapshot.counters.get(FAILURE_COUNT, 0.0)
    total = success + failure
    if total == 0:
        return None
    return failure / total


def _detect_latency_anomaly(snapshot: MetricsSnapshot) -> Optional[str]:
    summary = snapshot.histograms.get(DEPLOY_DURATION_MS)
    if summary is None or summary.count < 3 or summary.avg <= 0:
        return None
    if summary.max > summary.avg * _LATENCY_ANOMALY_FACTOR:
        return (
            f"deployment latency spike detected: max {summary.max:.0f}ms "
            f"vs avg {summary.avg:.0f}ms"
        )
    return None


@dataclass(frozen=True)
class Insight:
    """One immutable, actionable observation about a deployment."""

    insight_id: str
    deployment: str
    category: str
    severity: str
    summary: str
    recommendation: str
    detected_at: datetime

    def to_dict(self) -> dict:
        return {
            "insight_id": self.insight_id,
            "deployment": self.deployment,
            "category": self.category,
            "severity": self.severity,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "detected_at": self.detected_at.isoformat(),
        }


@dataclass(frozen=True)
class InsightReport:
    """One immutable snapshot of the insights derived for a deployment."""

    deployment: str
    generated_at: datetime
    insights: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "deployment": self.deployment,
            "generated_at": self.generated_at.isoformat(),
            "insights": [insight.to_dict() for insight in self.insights],
        }


class DeploymentInsightsService:
    """Derives actionable operational insights from observability data."""

    def __init__(self) -> None:
        self._history: list[InsightReport] = []
        self._latest: dict[str, InsightReport] = {}
        self._baselines: dict[str, dict[str, float]] = {}
        self._lock = Lock()

    def analyze(
        self,
        deployment: str,
        *,
        slo_manager: Optional[DeploymentSLOManager] = None,
        capacity_monitor: Optional[DeploymentCapacityMonitor] = None,
        recovery_coordinator: Optional[DeploymentRecoveryCoordinator] = None,
        metrics_collector: Optional[DeploymentMetricsCollector] = None,
        timestamp: Optional[datetime] = None,
    ) -> InsightReport:
        if not deployment:
            raise ValueError("deployment identifier is required")

        now = timestamp or datetime.now(timezone.utc)
        insights: list[Insight] = []

        if slo_manager is not None:
            for result in slo_manager.status():
                if result.status == "HEALTHY":
                    continue
                severity = "CRITICAL" if result.status == "BREACHED" else "WARNING"
                insights.append(
                    self._make(
                        deployment,
                        "RELIABILITY",
                        severity,
                        f"{result.objective_name} is {result.status} "
                        f"(value={result.value:.3f}, target={result.target})",
                        "Investigate the underlying cause and consider rolling "
                        "back or throttling recent changes.",
                        now,
                    )
                )

        with self._lock:
            baseline = self._baselines.setdefault(deployment, {})

        if metrics_collector is not None:
            snapshot = metrics_collector.snapshot()

            avg_latency = snapshot.get(DEPLOY_DURATION_MS, histogram_field="avg")
            if avg_latency is not None:
                previous_latency = baseline.get("avg_latency_ms")
                if (
                    previous_latency is not None
                    and avg_latency > previous_latency * _LATENCY_TREND_FACTOR
                ):
                    insights.append(
                        self._make(
                            deployment,
                            "PERFORMANCE",
                            "WARNING",
                            f"deployment latency trending upward "
                            f"({previous_latency:.0f}ms -> {avg_latency:.0f}ms)",
                            "Profile recent changes for regressions or scale "
                            "out the affected service.",
                            now,
                        )
                    )
                baseline["avg_latency_ms"] = avg_latency

            anomaly_summary = _detect_latency_anomaly(snapshot)
            if anomaly_summary is not None:
                insights.append(
                    self._make(
                        deployment,
                        "PERFORMANCE",
                        "WARNING",
                        anomaly_summary,
                        "Investigate the outlier request(s) behind the latency spike.",
                        now,
                    )
                )

            failure_rate = _failure_rate(snapshot)
            if failure_rate is not None:
                previous_rate = baseline.get("failure_rate")
                if (
                    previous_rate is not None
                    and failure_rate > previous_rate + _FAILURE_RATE_TREND_DELTA
                ):
                    insights.append(
                        self._make(
                            deployment,
                            "DEPLOYMENT_TRENDS",
                            "WARNING",
                            f"deployment failure rate trending upward "
                            f"({previous_rate:.0%} -> {failure_rate:.0%})",
                            "Pause further rollouts until the regression is identified.",
                            now,
                        )
                    )
                baseline["failure_rate"] = failure_rate

        if capacity_monitor is not None:
            for resource in capacity_monitor.capacity():
                measurement = capacity_monitor.utilization(resource.name)
                if measurement is None or measurement.status == "OK":
                    continue
                severity = "CRITICAL" if measurement.status == "CRITICAL" else "WARNING"
                insights.append(
                    self._make(
                        deployment,
                        "CAPACITY",
                        severity,
                        f"{resource.name} utilization at "
                        f"{measurement.utilization:.0%} ({measurement.status})",
                        f"Provision additional {resource.name} capacity or "
                        "reduce load before the next rollout.",
                        now,
                    )
                )

        if recovery_coordinator is not None:
            for record in recovery_coordinator.history(deployment=deployment):
                if record.status != "FAILED":
                    continue
                insights.append(
                    self._make(
                        deployment,
                        "RECOVERY",
                        "CRITICAL",
                        f"recovery strategy '{record.strategy}' failed: {record.message}",
                        "Review the strategy registry and add a fallback "
                        "strategy for this failure mode.",
                        now,
                    )
                )

        report = InsightReport(
            deployment=deployment, generated_at=now, insights=tuple(insights)
        )
        with self._lock:
            self._history.append(report)
            self._latest[deployment] = report
        return report

    def recommend(self, deployment: str) -> list[str]:
        with self._lock:
            report = self._latest.get(deployment)
        if report is None:
            raise UnknownDeploymentError(deployment)
        return [insight.recommendation for insight in report.insights]

    def summary(self, deployment: Optional[str] = None) -> dict:
        with self._lock:
            if deployment is not None:
                report = self._latest.get(deployment)
                reports = [report] if report is not None else []
            else:
                reports = list(self._latest.values())

        by_category = {category: 0 for category in INSIGHT_CATEGORIES}
        by_severity = {severity: 0 for severity in INSIGHT_SEVERITIES}
        total = 0
        for report in reports:
            for insight in report.insights:
                by_category[insight.category] += 1
                by_severity[insight.severity] += 1
                total += 1

        return {
            "deployment": deployment,
            "total_insights": total,
            "by_category": by_category,
            "by_severity": by_severity,
        }

    def history(self, deployment: Optional[str] = None) -> list[InsightReport]:
        with self._lock:
            reports = list(self._history)
        if deployment is not None:
            reports = [r for r in reports if r.deployment == deployment]
        return reports

    def _make(
        self,
        deployment: str,
        category: str,
        severity: str,
        summary: str,
        recommendation: str,
        timestamp: datetime,
    ) -> Insight:
        return Insight(
            insight_id=_new_id(),
            deployment=deployment,
            category=category,
            severity=severity,
            summary=summary,
            recommendation=recommendation,
            detected_at=timestamp,
        )


_service = DeploymentInsightsService()


def get_deployment_insights_service() -> DeploymentInsightsService:
    return _service


router = APIRouter(prefix="/governance", tags=["governance-insights"])


@router.post("/insights/analyze")
def analyze_insights(payload: dict = Body(...)) -> dict:
    from .deployment_capacity import get_deployment_capacity_monitor
    from .deployment_metrics import get_deployment_metrics_collector
    from .deployment_recovery import get_deployment_recovery_coordinator
    from .deployment_slo import get_deployment_slo_manager

    deployment = payload.get("deployment")
    if not deployment:
        raise HTTPException(status_code=422, detail="deployment is required")
    report = get_deployment_insights_service().analyze(
        deployment,
        slo_manager=get_deployment_slo_manager(),
        capacity_monitor=get_deployment_capacity_monitor(),
        recovery_coordinator=get_deployment_recovery_coordinator(),
        metrics_collector=get_deployment_metrics_collector(),
    )
    return report.to_dict()


@router.get("/insights/summary")
def insights_summary(deployment: Optional[str] = Query(default=None)) -> dict:
    return get_deployment_insights_service().summary(deployment=deployment)


@router.get("/insights/history")
def insights_history(deployment: Optional[str] = Query(default=None)) -> list[dict]:
    reports = get_deployment_insights_service().history(deployment=deployment)
    return [report.to_dict() for report in reports]
