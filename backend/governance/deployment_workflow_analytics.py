from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Query

from .deployment_workflow import DeploymentWorkflowEngine

DEFAULT_BUCKET_SECONDS = 3600.0


@dataclass(frozen=True)
class WorkflowAnalytics:
    """An immutable summary of execution performance for one pipeline."""

    pipeline: str
    total_executions: int
    succeeded: int
    failed: int
    cancelled: int
    success_rate: float
    failure_rate: float
    average_duration_seconds: Optional[float] = None
    average_queue_seconds: Optional[float] = None
    stage_durations: dict = field(default_factory=dict)
    generated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "pipeline": self.pipeline,
            "total_executions": self.total_executions,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "success_rate": self.success_rate,
            "failure_rate": self.failure_rate,
            "average_duration_seconds": self.average_duration_seconds,
            "average_queue_seconds": self.average_queue_seconds,
            "stage_durations": dict(self.stage_durations),
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }


@dataclass(frozen=True)
class WorkflowTrend:
    """One immutable bucketed data point in a pipeline's performance trend over time."""

    pipeline: str
    bucket_start: datetime
    bucket_end: datetime
    total_executions: int
    succeeded: int
    failed: int
    average_duration_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "pipeline": self.pipeline,
            "bucket_start": self.bucket_start.isoformat(),
            "bucket_end": self.bucket_end.isoformat(),
            "total_executions": self.total_executions,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "average_duration_seconds": self.average_duration_seconds,
        }


class DeploymentWorkflowAnalyticsService:
    """Records completed workflow executions and computes performance analytics over them."""

    def __init__(self, workflow_engine: Optional[DeploymentWorkflowEngine] = None) -> None:
        self._records: dict[str, list] = {}
        self._lock = Lock()
        self._workflow_engine = workflow_engine

    def record(
        self,
        execution_id: str,
        *,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        queue_seconds: Optional[float] = None,
        stage_durations: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> WorkflowAnalytics:
        engine = workflow_engine or self._workflow_engine
        if engine is None:
            raise ValueError("workflow_engine is required")

        execution = engine.status(execution_id)
        now = timestamp or datetime.now(timezone.utc)
        entry = {
            "execution_id": execution_id,
            "status": execution.status,
            "duration_seconds": execution.duration_seconds(),
            "queue_seconds": queue_seconds,
            "stage_durations": dict(stage_durations or {}),
            "recorded_at": now,
        }
        with self._lock:
            self._records.setdefault(execution.pipeline, []).append(entry)

        return self.summarize(execution.pipeline, timestamp=now)

    def summarize(self, pipeline: Optional[str] = None, *, timestamp: Optional[datetime] = None):
        now = timestamp or datetime.now(timezone.utc)
        if pipeline is not None:
            with self._lock:
                entries = list(self._records.get(pipeline, ()))
            return self._summarize_entries(pipeline, entries, now)

        with self._lock:
            names = sorted(self._records)
            snapshot = {name: list(self._records[name]) for name in names}
        return tuple(self._summarize_entries(name, snapshot[name], now) for name in names)

    def trends(
        self,
        pipeline: Optional[str] = None,
        *,
        bucket_seconds: float = DEFAULT_BUCKET_SECONDS,
    ) -> tuple:
        if bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be positive")

        with self._lock:
            if pipeline is not None:
                pipelines = {pipeline: list(self._records.get(pipeline, ()))}
            else:
                pipelines = {name: list(entries) for name, entries in self._records.items()}

        results = []
        for name, entries in pipelines.items():
            buckets: dict[int, list] = {}
            for entry in entries:
                index = int(entry["recorded_at"].timestamp() // bucket_seconds)
                buckets.setdefault(index, []).append(entry)

            for index in sorted(buckets):
                bucket_entries = buckets[index]
                bucket_start = datetime.fromtimestamp(index * bucket_seconds, tz=timezone.utc)
                durations = [
                    e["duration_seconds"] for e in bucket_entries if e["duration_seconds"] is not None
                ]
                results.append(
                    WorkflowTrend(
                        pipeline=name,
                        bucket_start=bucket_start,
                        bucket_end=bucket_start + timedelta(seconds=bucket_seconds),
                        total_executions=len(bucket_entries),
                        succeeded=sum(1 for e in bucket_entries if e["status"] == "COMPLETED"),
                        failed=sum(1 for e in bucket_entries if e["status"] == "FAILED"),
                        average_duration_seconds=(
                            sum(durations) / len(durations) if durations else None
                        ),
                    )
                )

        return tuple(sorted(results, key=lambda trend: (trend.pipeline, trend.bucket_start)))

    def history(self, pipeline: str) -> tuple:
        with self._lock:
            entries = list(self._records.get(pipeline, ()))
        return tuple({**entry, "recorded_at": entry["recorded_at"].isoformat()} for entry in entries)

    def _summarize_entries(self, pipeline: str, entries: list, generated_at: datetime) -> WorkflowAnalytics:
        total = len(entries)
        succeeded = sum(1 for entry in entries if entry["status"] == "COMPLETED")
        failed = sum(1 for entry in entries if entry["status"] == "FAILED")
        cancelled = sum(1 for entry in entries if entry["status"] == "CANCELLED")

        durations = [entry["duration_seconds"] for entry in entries if entry["duration_seconds"] is not None]
        queue_times = [entry["queue_seconds"] for entry in entries if entry["queue_seconds"] is not None]

        stage_totals: dict[str, list] = {}
        for entry in entries:
            for stage, duration in entry["stage_durations"].items():
                stage_totals.setdefault(stage, []).append(duration)

        return WorkflowAnalytics(
            pipeline=pipeline,
            total_executions=total,
            succeeded=succeeded,
            failed=failed,
            cancelled=cancelled,
            success_rate=(succeeded / total) if total else 0.0,
            failure_rate=(failed / total) if total else 0.0,
            average_duration_seconds=(sum(durations) / len(durations)) if durations else None,
            average_queue_seconds=(sum(queue_times) / len(queue_times)) if queue_times else None,
            stage_durations={
                stage: sum(values) / len(values) for stage, values in stage_totals.items()
            },
            generated_at=generated_at,
        )


_service = DeploymentWorkflowAnalyticsService()


def get_deployment_workflow_analytics_service() -> DeploymentWorkflowAnalyticsService:
    return _service


router = APIRouter(prefix="/governance", tags=["governance-workflow-analytics"])


@router.get("/workflow-analytics")
def list_analytics() -> list:
    return [item.to_dict() for item in get_deployment_workflow_analytics_service().summarize()]


@router.get("/workflow-analytics/trends")
def get_trends(
    pipeline: Optional[str] = Query(default=None),
    bucket_seconds: float = Query(default=DEFAULT_BUCKET_SECONDS),
) -> list:
    trends = get_deployment_workflow_analytics_service().trends(
        pipeline, bucket_seconds=bucket_seconds
    )
    return [trend.to_dict() for trend in trends]


@router.get("/workflow-analytics/{workflow}")
def get_analytics(workflow: str) -> dict:
    return get_deployment_workflow_analytics_service().summarize(workflow).to_dict()
