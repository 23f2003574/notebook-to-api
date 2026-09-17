from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.observability.metrics_storage import MetricsStorageEngine


VALID_ANALYTICS_METRICS = (
    "request_rate",
    "error_rate",
    "alert_count",
    "trace_latency",
    "service_availability",
)


@dataclass
class ObservabilityMetrics:
    metric_name: str
    value: float
    timestamp: str


@dataclass
class AnalyticsSnapshot:
    generated_at: str
    metrics: Dict[str, float] = field(default_factory=dict)


class ObservabilityAnalyticsService:
    def __init__(self, storage_engine: Optional[MetricsStorageEngine] = None):
        self._storage_engine = storage_engine or MetricsStorageEngine()
        self._records: Dict[str, List[ObservabilityMetrics]] = {
            name: [] for name in VALID_ANALYTICS_METRICS
        }

    def record(
        self,
        metric_name: str,
        value: float,
        timestamp: Optional[str] = None,
    ) -> ObservabilityMetrics:
        if metric_name not in VALID_ANALYTICS_METRICS:
            raise ValueError(
                f"Unsupported analytics metric '{metric_name}'. "
                f"Expected one of {VALID_ANALYTICS_METRICS}."
            )

        entry = ObservabilityMetrics(
            metric_name=metric_name,
            value=value,
            timestamp=timestamp or _utc_now_iso(),
        )
        self._records[metric_name].append(entry)
        self._storage_engine.write(metric_name, entry.timestamp, value)
        return entry

    def latest(self, metric_name: str) -> Optional[float]:
        if metric_name not in VALID_ANALYTICS_METRICS:
            raise ValueError(
                f"Unsupported analytics metric '{metric_name}'. "
                f"Expected one of {VALID_ANALYTICS_METRICS}."
            )
        entries = self._records.get(metric_name, [])
        return entries[-1].value if entries else None

    def summary(self) -> AnalyticsSnapshot:
        averages = {
            name: sum(entry.value for entry in entries) / len(entries)
            for name, entries in self._records.items()
            if entries
        }
        return AnalyticsSnapshot(generated_at=_utc_now_iso(), metrics=averages)

    def trends(self, metric_name: str) -> List[Dict]:
        if metric_name not in VALID_ANALYTICS_METRICS:
            raise ValueError(
                f"Unsupported analytics metric '{metric_name}'. "
                f"Expected one of {VALID_ANALYTICS_METRICS}."
            )
        return self._storage_engine.read(metric_name)

    def export(self) -> Dict[str, List[Dict]]:
        return {
            name: [{"value": entry.value, "timestamp": entry.timestamp} for entry in entries]
            for name, entries in self._records.items()
            if entries
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
