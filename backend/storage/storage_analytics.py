from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .artifact_manager import ArtifactManager, get_artifact_manager
from .object_storage import ObjectStorageEngine, get_object_storage_engine
from .storage_gc import StorageGarbageCollector, get_storage_garbage_collector
from .storage_replication import StorageReplicationEngine, get_storage_replication_engine

_TREND_METRICS = (
    "storage_usage_bytes",
    "object_count",
    "artifact_count",
    "replication_rate",
    "gc_efficiency",
)


@dataclass(frozen=True)
class StorageMetrics:
    """A single point-in-time snapshot of storage-wide metrics."""

    storage_usage_bytes: int
    object_count: int
    artifact_count: int
    replication_rate: float
    gc_efficiency: float
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "storage_usage_bytes": self.storage_usage_bytes,
            "object_count": self.object_count,
            "artifact_count": self.artifact_count,
            "replication_rate": self.replication_rate,
            "gc_efficiency": self.gc_efficiency,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class StorageTrend:
    """The trajectory of a single metric across recorded snapshots."""

    metric: str
    points: tuple
    direction: str
    change: float

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "points": [dict(point) for point in self.points],
            "direction": self.direction,
            "change": self.change,
        }


class StorageAnalyticsService:
    """Aggregates utilization, growth, replication, and GC metrics over time."""

    def __init__(
        self,
        *,
        object_storage: ObjectStorageEngine,
        artifact_manager: ArtifactManager,
        replication_engine: Optional[StorageReplicationEngine] = None,
        gc: Optional[StorageGarbageCollector] = None,
    ) -> None:
        self._object_storage = object_storage
        self._artifact_manager = artifact_manager
        self._replication_engine = replication_engine
        self._gc = gc
        self._history: list = []
        self._lock = Lock()

    def record(self) -> StorageMetrics:
        keys = self._object_storage.list_keys()
        storage_usage_bytes = sum(self._object_storage.get(key).metadata.size for key in keys)

        metrics = StorageMetrics(
            storage_usage_bytes=storage_usage_bytes,
            object_count=len(keys),
            artifact_count=len(self._artifact_manager.list_artifacts()),
            replication_rate=self._replication_rate(),
            gc_efficiency=self._gc_efficiency(),
        )
        with self._lock:
            self._history.append(metrics)
        return metrics

    def summary(self) -> StorageMetrics:
        """Return the most recent snapshot, recording one now if none exists yet."""
        with self._lock:
            if self._history:
                return self._history[-1]
        return self.record()

    def trends(self, *, metric: Optional[str] = None) -> list:
        with self._lock:
            history = list(self._history)
        if len(history) < 2:
            return []

        names = [metric] if metric else list(_TREND_METRICS)
        trends = []
        for name in names:
            series = [(snapshot.recorded_at, getattr(snapshot, name)) for snapshot in history]
            change = series[-1][1] - series[0][1]
            if change > 0:
                direction = "increasing"
            elif change < 0:
                direction = "decreasing"
            else:
                direction = "flat"
            trends.append(
                StorageTrend(
                    metric=name,
                    points=tuple({"recorded_at": ts.isoformat(), "value": value} for ts, value in series),
                    direction=direction,
                    change=change,
                )
            )
        return trends

    def forecast(self, *, metric: str = "storage_usage_bytes", periods_ahead: int = 1) -> dict:
        """Project a metric's near-term value via linear extrapolation from recorded history."""
        if periods_ahead < 1:
            raise ValueError("periods_ahead must be >= 1")
        with self._lock:
            history = list(self._history)
        if len(history) < 2:
            raise ValueError("at least two recorded snapshots are required to forecast")

        values = [getattr(snapshot, metric) for snapshot in history]
        deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
        average_delta = sum(deltas) / len(deltas)
        projected = values[-1] + average_delta * periods_ahead

        return {
            "metric": metric,
            "current": values[-1],
            "projected": projected,
            "periods_ahead": periods_ahead,
        }

    def _replication_rate(self) -> float:
        if self._replication_engine is None:
            return 0.0
        statuses = self._replication_engine.list_status()
        if not statuses:
            return 1.0
        in_sync = sum(1 for status in statuses if status.in_sync)
        return in_sync / len(statuses)

    def _gc_efficiency(self) -> float:
        if self._gc is None:
            return 0.0
        report = self._gc.report()
        if report is None or report.candidates_found == 0:
            return 0.0
        return report.objects_removed / report.candidates_found


_storage_analytics_service = StorageAnalyticsService(
    object_storage=get_object_storage_engine(),
    artifact_manager=get_artifact_manager(),
    replication_engine=get_storage_replication_engine(),
    gc=get_storage_garbage_collector(),
)


def get_storage_analytics_service() -> StorageAnalyticsService:
    return _storage_analytics_service


router = APIRouter(prefix="/storage/analytics", tags=["storage-analytics"])


@router.get("")
def record_endpoint(
    service: StorageAnalyticsService = Depends(get_storage_analytics_service),
) -> dict:
    return service.record().to_dict()


@router.get("/summary")
def summary_endpoint(
    service: StorageAnalyticsService = Depends(get_storage_analytics_service),
) -> dict:
    return service.summary().to_dict()


@router.get("/trends")
def trends_endpoint(
    metric: Optional[str] = None,
    service: StorageAnalyticsService = Depends(get_storage_analytics_service),
) -> list:
    return [trend.to_dict() for trend in service.trends(metric=metric)]


@router.get("/forecast")
def forecast_endpoint(
    metric: str = "storage_usage_bytes",
    periods_ahead: int = 1,
    service: StorageAnalyticsService = Depends(get_storage_analytics_service),
) -> dict:
    try:
        return service.forecast(metric=metric, periods_ahead=periods_ahead)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
