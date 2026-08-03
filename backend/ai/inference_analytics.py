from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

_BUCKET_FORMATS = {
    "hour": "%Y-%m-%dT%H:00:00Z",
    "day": "%Y-%m-%d",
}


@dataclass(frozen=True)
class InferenceMetrics:
    """A single recorded data point about an inference request."""

    model_name: str
    status: str
    latency_ms: float
    token_count: int
    recorded_at: datetime

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class InferenceTrend:
    """A time-bucketed aggregate of inference metrics."""

    bucket: str
    model_name: Optional[str]
    request_count: int
    success_rate: float
    average_latency_ms: Optional[float]

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "model_name": self.model_name,
            "request_count": self.request_count,
            "success_rate": self.success_rate,
            "average_latency_ms": self.average_latency_ms,
        }


class InferenceAnalyticsService:
    """Collects inference, routing, deployment, and batch metrics into summaries and trends."""

    def __init__(self) -> None:
        self._records: list = []
        self._lock = Lock()

    def record(
        self,
        model_name: str,
        status: str,
        latency_ms: float,
        token_count: int = 0,
        *,
        recorded_at: Optional[datetime] = None,
    ) -> InferenceMetrics:
        record = InferenceMetrics(
            model_name=model_name,
            status=status,
            latency_ms=latency_ms,
            token_count=token_count,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
        with self._lock:
            self._records.append(record)
        return record

    def _filtered_records(self, model_name: Optional[str] = None) -> list:
        with self._lock:
            records = list(self._records)
        if model_name is not None:
            records = [record for record in records if record.model_name == model_name]
        return records

    def list_records(self, model_name: Optional[str] = None) -> list:
        return self._filtered_records(model_name)

    def summary(self, model_name: Optional[str] = None) -> dict:
        records = self._filtered_records(model_name)
        request_count = len(records)
        success_count = sum(1 for record in records if record.status == "success")
        success_rate = success_count / request_count if request_count else 0.0

        latencies = [record.latency_ms for record in records]
        average_latency_ms = sum(latencies) / len(latencies) if latencies else None

        total_tokens = sum(record.token_count for record in records)
        total_duration_seconds = sum(latencies) / 1000
        token_throughput_per_second = (
            total_tokens / total_duration_seconds if total_duration_seconds else None
        )

        model_utilization = {}
        if model_name is None:
            for record in records:
                model_utilization[record.model_name] = model_utilization.get(record.model_name, 0) + 1
            if request_count:
                model_utilization = {
                    name: count / request_count for name, count in model_utilization.items()
                }

        return {
            "model_name": model_name,
            "request_count": request_count,
            "success_rate": success_rate,
            "average_latency_ms": average_latency_ms,
            "token_throughput_per_second": token_throughput_per_second,
            "model_utilization": model_utilization,
        }

    @staticmethod
    def _bucket_key(timestamp: datetime, bucket: str) -> str:
        try:
            fmt = _BUCKET_FORMATS[bucket]
        except KeyError:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        return timestamp.strftime(fmt)

    def trends(self, model_name: Optional[str] = None, *, bucket: str = "day") -> list:
        if bucket not in _BUCKET_FORMATS:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        records = self._filtered_records(model_name)
        grouped: dict = {}
        for record in records:
            key = self._bucket_key(record.recorded_at, bucket)
            grouped.setdefault(key, []).append(record)

        results = []
        for bucket_key, items in grouped.items():
            success_count = sum(1 for item in items if item.status == "success")
            latencies = [item.latency_ms for item in items]
            results.append(
                InferenceTrend(
                    bucket=bucket_key,
                    model_name=model_name,
                    request_count=len(items),
                    success_rate=success_count / len(items),
                    average_latency_ms=sum(latencies) / len(latencies) if latencies else None,
                )
            )
        return sorted(results, key=lambda trend: trend.bucket)

    def export(self, model_name: Optional[str] = None, *, format: str = "json"):
        """Build an export-ready report: current summary, trends, and raw records."""
        records = self._filtered_records(model_name)
        if format == "json":
            return {
                "summary": self.summary(model_name),
                "trends": [trend.to_dict() for trend in self.trends(model_name)],
                "records": [record.to_dict() for record in records],
            }
        if format == "csv":
            return self._records_to_csv(records)
        raise ValueError(f"unsupported export format '{format}'")

    @staticmethod
    def _records_to_csv(records: list) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=["model_name", "status", "latency_ms", "token_count", "recorded_at"]
        )
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_dict())
        return buffer.getvalue()


_inference_analytics_service = InferenceAnalyticsService()


def get_inference_analytics_service() -> InferenceAnalyticsService:
    return _inference_analytics_service


router = APIRouter(prefix="/ai/analytics", tags=["inference-analytics"])


@router.get("")
def list_metrics_endpoint(
    model_name: Optional[str] = Query(default=None),
    service: InferenceAnalyticsService = Depends(get_inference_analytics_service),
) -> list:
    return [record.to_dict() for record in service.list_records(model_name)]


@router.get("/summary")
def summary_endpoint(
    model_name: Optional[str] = Query(default=None),
    service: InferenceAnalyticsService = Depends(get_inference_analytics_service),
) -> dict:
    return service.summary(model_name)


@router.get("/trends")
def trends_endpoint(
    model_name: Optional[str] = Query(default=None),
    bucket: str = Query(default="day"),
    service: InferenceAnalyticsService = Depends(get_inference_analytics_service),
) -> list:
    try:
        trends = service.trends(model_name, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [trend.to_dict() for trend in trends]
