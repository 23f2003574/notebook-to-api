from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


VALID_STORAGE_TYPES = ("time_series", "aggregated", "raw_samples", "archived")


@dataclass
class MetricSeries:
    metric_name: str
    storage_type: str = "time_series"
    points: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if self.storage_type not in VALID_STORAGE_TYPES:
            raise ValueError(
                f"Unsupported storage_type '{self.storage_type}'. "
                f"Expected one of {VALID_STORAGE_TYPES}."
            )


@dataclass
class RetentionPolicy:
    max_age_seconds: Optional[float] = None
    max_points: Optional[int] = None


class MetricsStorageEngine:
    def __init__(self, retention_policy: Optional[RetentionPolicy] = None):
        self._series: Dict[str, MetricSeries] = {}
        self._retention_policy = retention_policy or RetentionPolicy()

    def write(
        self,
        metric_name: str,
        timestamp: str,
        value: float,
        storage_type: str = "time_series",
    ) -> MetricSeries:
        series = self._series.get(metric_name)
        if series is None:
            series = MetricSeries(metric_name=metric_name, storage_type=storage_type)
            self._series[metric_name] = series

        series.points.append({"timestamp": timestamp, "value": value})
        self._enforce_retention(series, self._retention_policy)
        return series

    def read(
        self,
        metric_name: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Dict]:
        series = self._series.get(metric_name)
        if series is None:
            raise KeyError(f"No stored series for '{metric_name}'")

        points = series.points
        if start is not None:
            points = [point for point in points if point["timestamp"] >= start]
        if end is not None:
            points = [point for point in points if point["timestamp"] <= end]
        return list(points)

    def compact(self, metric_name: str, bucket_size: int) -> MetricSeries:
        series = self._series.get(metric_name)
        if series is None:
            raise KeyError(f"No stored series for '{metric_name}'")
        if bucket_size <= 0:
            raise ValueError("bucket_size must be positive")

        compacted_points = []
        for i in range(0, len(series.points), bucket_size):
            bucket = series.points[i:i + bucket_size]
            average_value = sum(point["value"] for point in bucket) / len(bucket)
            compacted_points.append({"timestamp": bucket[0]["timestamp"], "value": average_value})

        series.points = compacted_points
        series.storage_type = "aggregated"
        return series

    def retention(self, policy: Optional[RetentionPolicy] = None) -> Dict[str, int]:
        policy = policy or self._retention_policy
        removed_counts = {}
        for name, series in self._series.items():
            original_length = len(series.points)
            self._enforce_retention(series, policy)
            removed_counts[name] = original_length - len(series.points)
        return removed_counts

    def _enforce_retention(self, series: MetricSeries, policy: RetentionPolicy) -> None:
        if policy.max_points is not None and len(series.points) > policy.max_points:
            series.points = series.points[-policy.max_points:]

        if policy.max_age_seconds is not None:
            cutoff = _now_epoch() - policy.max_age_seconds
            series.points = [
                point for point in series.points if _parse_epoch(point["timestamp"]) >= cutoff
            ]


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _parse_epoch(timestamp: str) -> float:
    return datetime.fromisoformat(timestamp).timestamp()
