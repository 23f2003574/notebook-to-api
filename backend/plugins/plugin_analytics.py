from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query


class MetricType(str, Enum):
    INSTALL = "install"
    ACTIVATION = "activation"
    LOAD = "load"
    EXECUTION = "execution"


_BUCKET_FORMATS = {
    "hour": "%Y-%m-%dT%H:00:00Z",
    "day": "%Y-%m-%d",
}


@dataclass(frozen=True)
class PluginMetrics:
    """A single recorded data point about a plugin's usage or runtime behavior."""

    plugin: str
    metric_type: MetricType
    value: Optional[float]
    success: Optional[bool]
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "metric_type": self.metric_type.value,
            "value": self.value,
            "success": self.success,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class PluginTrend:
    """A time-bucketed aggregate of one metric type."""

    bucket: str
    metric_type: MetricType
    plugin: Optional[str]
    count: int
    average_value: Optional[float]

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "metric_type": self.metric_type.value,
            "plugin": self.plugin,
            "count": self.count,
            "average_value": self.average_value,
        }


class PluginAnalyticsService:
    """Collects plugin usage/runtime metrics and aggregates them into summaries and trends."""

    def __init__(self) -> None:
        self._records: list = []
        self._lock = Lock()

    def record(
        self,
        plugin: str,
        metric_type,
        value: Optional[float] = None,
        *,
        success: Optional[bool] = None,
        timestamp: Optional[datetime] = None,
    ) -> PluginMetrics:
        metric_type = metric_type if isinstance(metric_type, MetricType) else MetricType(metric_type)
        record = PluginMetrics(
            plugin=plugin,
            metric_type=metric_type,
            value=value,
            success=success,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._records.append(record)
        return record

    def _filtered_records(self, plugin: Optional[str] = None, metric_type=None) -> list:
        with self._lock:
            records = list(self._records)
        if plugin is not None:
            records = [record for record in records if record.plugin == plugin]
        if metric_type is not None:
            metric_type = metric_type if isinstance(metric_type, MetricType) else MetricType(metric_type)
            records = [record for record in records if record.metric_type == metric_type]
        return records

    def list_records(self, plugin: Optional[str] = None, metric_type=None) -> list:
        return self._filtered_records(plugin, metric_type)

    def recent(self, limit: int = 10, plugin: Optional[str] = None) -> list:
        """The most recently recorded metrics (by timestamp, not insertion order), newest first."""
        records = self._filtered_records(plugin)
        return sorted(records, key=lambda record: record.timestamp, reverse=True)[:limit]

    def summary(self, plugin: Optional[str] = None) -> dict:
        records = self._filtered_records(plugin)
        install_count = sum(1 for record in records if record.metric_type == MetricType.INSTALL)
        active_plugins = len(
            {record.plugin for record in records if record.metric_type == MetricType.ACTIVATION}
        )
        load_times = [
            record.value for record in records if record.metric_type == MetricType.LOAD and record.value is not None
        ]
        average_load_time = sum(load_times) / len(load_times) if load_times else None
        executions = [record for record in records if record.metric_type == MetricType.EXECUTION]
        execution_count = len(executions)
        failures = sum(1 for record in executions if record.success is False)
        failure_rate = failures / execution_count if execution_count else 0.0
        return {
            "plugin": plugin,
            "install_count": install_count,
            "active_plugins": active_plugins,
            "average_load_time": average_load_time,
            "execution_count": execution_count,
            "failure_rate": failure_rate,
        }

    @staticmethod
    def _bucket_key(timestamp: datetime, bucket: str) -> str:
        try:
            fmt = _BUCKET_FORMATS[bucket]
        except KeyError:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        return timestamp.strftime(fmt)

    def trends(self, plugin: Optional[str] = None, metric_type=None, *, bucket: str = "day") -> list:
        if bucket not in _BUCKET_FORMATS:
            raise ValueError(f"unsupported bucket size '{bucket}'; expected one of {sorted(_BUCKET_FORMATS)}")
        records = self._filtered_records(plugin, metric_type)
        grouped: dict = {}
        for record in records:
            key = (self._bucket_key(record.timestamp, bucket), record.metric_type)
            grouped.setdefault(key, []).append(record)

        results = []
        for (bucket_key, m_type), items in grouped.items():
            values = [item.value for item in items if item.value is not None]
            average_value = sum(values) / len(values) if values else None
            results.append(
                PluginTrend(
                    bucket=bucket_key,
                    metric_type=m_type,
                    plugin=plugin,
                    count=len(items),
                    average_value=average_value,
                )
            )
        return sorted(results, key=lambda trend: (trend.bucket, trend.metric_type.value))

    def export(self, plugin: Optional[str] = None, *, format: str = "json"):
        """Build an export-ready report: current summary, trends, and raw records."""
        records = self._filtered_records(plugin)
        if format == "json":
            return {
                "summary": self.summary(plugin),
                "trends": [trend.to_dict() for trend in self.trends(plugin)],
                "records": [record.to_dict() for record in records],
            }
        if format == "csv":
            return self._records_to_csv(records)
        raise ValueError(f"unsupported export format '{format}'")

    @staticmethod
    def _records_to_csv(records: list) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["plugin", "metric_type", "value", "success", "timestamp"])
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_dict())
        return buffer.getvalue()


_plugin_analytics_service = PluginAnalyticsService()


def get_plugin_analytics_service() -> PluginAnalyticsService:
    return _plugin_analytics_service


router = APIRouter(prefix="/plugins/analytics", tags=["plugins-analytics"])


@router.get("")
def list_metrics_endpoint(
    plugin: Optional[str] = Query(default=None),
    metric_type: Optional[str] = Query(default=None),
    service: PluginAnalyticsService = Depends(get_plugin_analytics_service),
) -> list:
    try:
        records = service.list_records(plugin, metric_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [record.to_dict() for record in records]


@router.get("/summary")
def summary_endpoint(
    plugin: Optional[str] = Query(default=None),
    service: PluginAnalyticsService = Depends(get_plugin_analytics_service),
) -> dict:
    return service.summary(plugin)


@router.get("/trends")
def trends_endpoint(
    plugin: Optional[str] = Query(default=None),
    metric_type: Optional[str] = Query(default=None),
    bucket: str = Query(default="day"),
    service: PluginAnalyticsService = Depends(get_plugin_analytics_service),
) -> list:
    try:
        trends = service.trends(plugin, metric_type, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [trend.to_dict() for trend in trends]
