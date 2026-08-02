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
class PipelineMetrics:
    """A single recorded data point about a pipeline execution."""

    workflow_name: str
    status: str
    duration_ms: float
    row_count: int
    recorded_at: datetime

    def to_dict(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "row_count": self.row_count,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class PipelineTrend:
    """A time-bucketed aggregate of pipeline execution metrics."""

    bucket: str
    workflow_name: Optional[str]
    execution_count: int
    success_rate: float
    average_duration_ms: Optional[float]

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "workflow_name": self.workflow_name,
            "execution_count": self.execution_count,
            "success_rate": self.success_rate,
            "average_duration_ms": self.average_duration_ms,
        }


class PipelineAnalyticsService:
    """Collects pipeline execution metrics and aggregates them into summaries and trends."""

    def __init__(self) -> None:
        self._records: list = []
        self._lock = Lock()

    def record(
        self,
        workflow_name: str,
        status: str,
        duration_ms: float,
        row_count: int = 0,
        *,
        recorded_at: Optional[datetime] = None,
    ) -> PipelineMetrics:
        record = PipelineMetrics(
            workflow_name=workflow_name,
            status=status,
            duration_ms=duration_ms,
            row_count=row_count,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
        with self._lock:
            self._records.append(record)
        return record

    def _filtered_records(self, workflow_name: Optional[str] = None) -> list:
        with self._lock:
            records = list(self._records)
        if workflow_name is not None:
            records = [record for record in records if record.workflow_name == workflow_name]
        return records

    def list_records(self, workflow_name: Optional[str] = None) -> list:
        return self._filtered_records(workflow_name)

    def summary(self, workflow_name: Optional[str] = None) -> dict:
        records = self._filtered_records(workflow_name)
        execution_count = len(records)
        success_count = sum(1 for record in records if record.status == "success")
        failure_count = execution_count - success_count
        success_rate = success_count / execution_count if execution_count else 0.0
        failure_rate = failure_count / execution_count if execution_count else 0.0

        durations = [record.duration_ms for record in records]
        average_runtime_ms = sum(durations) / len(durations) if durations else None

        total_rows = sum(record.row_count for record in records)
        total_duration_seconds = sum(durations) / 1000
        data_throughput_rows_per_second = total_rows / total_duration_seconds if total_duration_seconds else None

        return {
            "workflow_name": workflow_name,
            "execution_count": execution_count,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
            "average_runtime_ms": average_runtime_ms,
            "data_throughput_rows_per_second": data_throughput_rows_per_second,
        }

    @staticmethod
    def _bucket_key(timestamp: datetime, bucket: str) -> str:
        try:
            fmt = _BUCKET_FORMATS[bucket]
        except KeyError:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        return timestamp.strftime(fmt)

    def trends(self, workflow_name: Optional[str] = None, *, bucket: str = "day") -> list:
        if bucket not in _BUCKET_FORMATS:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        records = self._filtered_records(workflow_name)
        grouped: dict = {}
        for record in records:
            key = self._bucket_key(record.recorded_at, bucket)
            grouped.setdefault(key, []).append(record)

        results = []
        for bucket_key, items in grouped.items():
            success_count = sum(1 for item in items if item.status == "success")
            durations = [item.duration_ms for item in items]
            results.append(
                PipelineTrend(
                    bucket=bucket_key,
                    workflow_name=workflow_name,
                    execution_count=len(items),
                    success_rate=success_count / len(items),
                    average_duration_ms=sum(durations) / len(durations) if durations else None,
                )
            )
        return sorted(results, key=lambda trend: trend.bucket)

    def export(self, workflow_name: Optional[str] = None, *, format: str = "json"):
        """Build an export-ready report: current summary, trends, and raw records."""
        records = self._filtered_records(workflow_name)
        if format == "json":
            return {
                "summary": self.summary(workflow_name),
                "trends": [trend.to_dict() for trend in self.trends(workflow_name)],
                "records": [record.to_dict() for record in records],
            }
        if format == "csv":
            return self._records_to_csv(records)
        raise ValueError(f"unsupported export format '{format}'")

    @staticmethod
    def _records_to_csv(records: list) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["workflow_name", "status", "duration_ms", "row_count", "recorded_at"])
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_dict())
        return buffer.getvalue()


_pipeline_analytics_service = PipelineAnalyticsService()


def get_pipeline_analytics_service() -> PipelineAnalyticsService:
    return _pipeline_analytics_service


router = APIRouter(prefix="/pipelines/analytics", tags=["pipeline-analytics"])


@router.get("")
def list_metrics_endpoint(
    workflow_name: Optional[str] = Query(default=None),
    service: PipelineAnalyticsService = Depends(get_pipeline_analytics_service),
) -> list:
    return [record.to_dict() for record in service.list_records(workflow_name)]


@router.get("/summary")
def summary_endpoint(
    workflow_name: Optional[str] = Query(default=None),
    service: PipelineAnalyticsService = Depends(get_pipeline_analytics_service),
) -> dict:
    return service.summary(workflow_name)


@router.get("/trends")
def trends_endpoint(
    workflow_name: Optional[str] = Query(default=None),
    bucket: str = Query(default="day"),
    service: PipelineAnalyticsService = Depends(get_pipeline_analytics_service),
) -> list:
    try:
        trends = service.trends(workflow_name, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [trend.to_dict() for trend in trends]
