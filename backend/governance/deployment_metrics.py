from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Mapping, Optional

from fastapi import APIRouter

DEPLOY_COUNT = "deploy_count"
DEPLOY_DURATION_MS = "deploy_duration_ms"
SUCCESS_COUNT = "success_count"
FAILURE_COUNT = "failure_count"
ACTIVE_DEPLOYMENTS = "active_deployments"
ROLLBACK_COUNT = "rollback_count"
AVAILABILITY = "availability"


@dataclass(frozen=True)
class HistogramSummary:
    """Immutable rollup of the observations recorded for a histogram."""

    count: int
    sum: float
    min: float
    max: float
    avg: float

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "sum": self.sum,
            "min": self.min,
            "max": self.max,
            "avg": self.avg,
        }


def _summarize(values: list[float]) -> HistogramSummary:
    return HistogramSummary(
        count=len(values),
        sum=sum(values),
        min=min(values),
        max=max(values),
        avg=sum(values) / len(values),
    )


@dataclass(frozen=True)
class MetricsSnapshot:
    """An immutable point-in-time view of all collected metrics."""

    timestamp: datetime
    counters: Mapping[str, float]
    gauges: Mapping[str, float]
    histograms: Mapping[str, HistogramSummary]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {
                name: summary.to_dict()
                for name, summary in self.histograms.items()
            },
        }

    def get(
        self,
        name: str,
        *,
        histogram_field: str = "avg",
        default: Optional[float] = None,
    ) -> Optional[float]:
        """
        Look up a single metric value by name, regardless of which
        kind (counter, gauge, or histogram) it was recorded as.
        """

        if name in self.counters:
            return self.counters[name]
        if name in self.gauges:
            return self.gauges[name]
        if name in self.histograms:
            return getattr(self.histograms[name], histogram_field)
        return default


class DeploymentMetricsCollector:
    """Lightweight counter/gauge/histogram collector for deployments."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._lock = Lock()

    def increment(self, name: str, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def record(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._histograms.setdefault(name, []).append(value)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {
                name: _summarize(values)
                for name, values in self._histograms.items()
            }
        return MetricsSnapshot(
            timestamp=datetime.now(timezone.utc),
            counters=counters,
            gauges=gauges,
            histograms=histograms,
        )

    def clear(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


_collector = DeploymentMetricsCollector()


def get_deployment_metrics_collector() -> DeploymentMetricsCollector:
    return _collector


router = APIRouter(prefix="/governance", tags=["governance-metrics"])


@router.get("/metrics")
def list_metrics() -> dict:
    snapshot = get_deployment_metrics_collector().snapshot()
    flattened: dict[str, float] = {}
    flattened.update(snapshot.counters)
    flattened.update(snapshot.gauges)
    for name, summary in snapshot.histograms.items():
        flattened[f"{name}_count"] = summary.count
        flattened[f"{name}_sum"] = summary.sum
    return flattened


@router.get("/metrics/snapshot")
def metrics_snapshot() -> dict:
    return get_deployment_metrics_collector().snapshot().to_dict()
