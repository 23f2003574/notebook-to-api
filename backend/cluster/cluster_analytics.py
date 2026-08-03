from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

_BUCKET_FORMATS = {
    "hour": "%Y-%m-%dT%H:00:00Z",
    "day": "%Y-%m-%d",
}


@dataclass(frozen=True)
class ClusterMetrics:
    """A single recorded snapshot of cluster load and outcomes for one capability pool."""

    capability: str
    worker_count: int
    active_jobs: int
    queue_depth: int
    completed_count: int
    failed_count: int
    scheduling_latency_ms: Optional[float]
    recorded_at: datetime

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "worker_count": self.worker_count,
            "active_jobs": self.active_jobs,
            "queue_depth": self.queue_depth,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "scheduling_latency_ms": self.scheduling_latency_ms,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class ClusterTrend:
    """A time-bucketed aggregate of cluster metrics."""

    bucket: str
    capability: Optional[str]
    sample_count: int
    average_worker_utilization: float
    average_queue_depth: float
    throughput: int
    failure_rate: float
    average_scheduling_latency_ms: Optional[float]

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "capability": self.capability,
            "sample_count": self.sample_count,
            "average_worker_utilization": self.average_worker_utilization,
            "average_queue_depth": self.average_queue_depth,
            "throughput": self.throughput,
            "failure_rate": self.failure_rate,
            "average_scheduling_latency_ms": self.average_scheduling_latency_ms,
        }


class ClusterAnalyticsService:
    """Collects worker utilization, throughput, scheduling latency, and failure metrics over time."""

    def __init__(self) -> None:
        self._records: list = []
        self._lock = Lock()

    def record(
        self,
        capability: str,
        *,
        worker_count: int,
        active_jobs: int,
        queue_depth: int,
        completed_count: int = 0,
        failed_count: int = 0,
        scheduling_latency_ms: Optional[float] = None,
        recorded_at: Optional[datetime] = None,
    ) -> ClusterMetrics:
        record = ClusterMetrics(
            capability=capability,
            worker_count=worker_count,
            active_jobs=active_jobs,
            queue_depth=queue_depth,
            completed_count=completed_count,
            failed_count=failed_count,
            scheduling_latency_ms=scheduling_latency_ms,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
        with self._lock:
            self._records.append(record)
        return record

    def _filtered_records(self, capability: Optional[str] = None) -> list:
        with self._lock:
            records = list(self._records)
        if capability is not None:
            records = [record for record in records if record.capability == capability]
        return records

    def list_records(self, capability: Optional[str] = None) -> list:
        return self._filtered_records(capability)

    def summary(self, capability: Optional[str] = None) -> dict:
        records = self._filtered_records(capability)
        total_completed = sum(record.completed_count for record in records)
        total_failed = sum(record.failed_count for record in records)
        total_jobs = total_completed + total_failed

        return {
            "capability": capability,
            "sample_count": len(records),
            "average_worker_utilization": self._average_utilization(records),
            "average_queue_depth": self._average([record.queue_depth for record in records]),
            "job_throughput": total_completed,
            "failure_rate": total_failed / total_jobs if total_jobs else 0.0,
            "average_scheduling_latency_ms": self._average_latency(records),
        }

    @staticmethod
    def _bucket_key(timestamp: datetime, bucket: str) -> str:
        try:
            fmt = _BUCKET_FORMATS[bucket]
        except KeyError:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        return timestamp.strftime(fmt)

    def trends(self, capability: Optional[str] = None, *, bucket: str = "day") -> list:
        if bucket not in _BUCKET_FORMATS:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")

        records = self._filtered_records(capability)
        grouped: dict = {}
        for record in records:
            key = self._bucket_key(record.recorded_at, bucket)
            grouped.setdefault(key, []).append(record)

        results = []
        for bucket_key, items in grouped.items():
            completed = sum(item.completed_count for item in items)
            failed = sum(item.failed_count for item in items)
            total = completed + failed
            results.append(
                ClusterTrend(
                    bucket=bucket_key,
                    capability=capability,
                    sample_count=len(items),
                    average_worker_utilization=self._average_utilization(items),
                    average_queue_depth=self._average([item.queue_depth for item in items]),
                    throughput=completed,
                    failure_rate=failed / total if total else 0.0,
                    average_scheduling_latency_ms=self._average_latency(items),
                )
            )
        return sorted(results, key=lambda trend: trend.bucket)

    def export(self, capability: Optional[str] = None, *, format: str = "json"):
        """Build an export-ready report: current summary, trends, and raw records."""
        records = self._filtered_records(capability)
        if format == "json":
            return {
                "summary": self.summary(capability),
                "trends": [trend.to_dict() for trend in self.trends(capability)],
                "records": [record.to_dict() for record in records],
            }
        if format == "csv":
            return self._records_to_csv(records)
        raise ValueError(f"unsupported export format '{format}'")

    @staticmethod
    def _average(values: list) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _average_utilization(records: list) -> float:
        utilizations = [record.active_jobs / record.worker_count for record in records if record.worker_count]
        return sum(utilizations) / len(utilizations) if utilizations else 0.0

    @staticmethod
    def _average_latency(records: list) -> Optional[float]:
        latencies = [record.scheduling_latency_ms for record in records if record.scheduling_latency_ms is not None]
        return sum(latencies) / len(latencies) if latencies else None

    @staticmethod
    def _records_to_csv(records: list) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "capability",
                "worker_count",
                "active_jobs",
                "queue_depth",
                "completed_count",
                "failed_count",
                "scheduling_latency_ms",
                "recorded_at",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_dict())
        return buffer.getvalue()


_cluster_analytics_service = ClusterAnalyticsService()


def get_cluster_analytics_service() -> ClusterAnalyticsService:
    return _cluster_analytics_service


router = APIRouter(prefix="/cluster/analytics", tags=["cluster-analytics"])


@router.get("")
def list_metrics_endpoint(
    capability: Optional[str] = None,
    service: ClusterAnalyticsService = Depends(get_cluster_analytics_service),
) -> list:
    return [record.to_dict() for record in service.list_records(capability)]


@router.get("/summary")
def summary_endpoint(
    capability: Optional[str] = None,
    service: ClusterAnalyticsService = Depends(get_cluster_analytics_service),
) -> dict:
    return service.summary(capability)


@router.get("/trends")
def trends_endpoint(
    capability: Optional[str] = None,
    bucket: str = "day",
    service: ClusterAnalyticsService = Depends(get_cluster_analytics_service),
) -> list:
    try:
        trends = service.trends(capability, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [trend.to_dict() for trend in trends]
