from dataclasses import dataclass, field
from typing import Dict, List, Optional


VALID_METRIC_TYPES = ("counter", "gauge", "histogram")


@dataclass
class MetricDefinition:
    name: str
    metric_type: str
    description: str = ""
    labels: List[str] = field(default_factory=list)
    histogram_buckets: Optional[List[float]] = None

    def __post_init__(self):
        if self.metric_type not in VALID_METRIC_TYPES:
            raise ValueError(
                f"Unsupported metric_type '{self.metric_type}'. "
                f"Expected one of {VALID_METRIC_TYPES}."
            )
        if self.metric_type != "histogram" and self.histogram_buckets:
            raise ValueError("histogram_buckets is only valid for histogram metrics")


@dataclass
class MetricSample:
    metric_name: str
    value: float
    timestamp: str
    labels: Dict[str, str] = field(default_factory=dict)


class MetricsRegistry:
    def __init__(self):
        self._definitions: Dict[str, MetricDefinition] = {}
        self._samples: Dict[str, List[MetricSample]] = {}

    def register_metric(self, definition: MetricDefinition) -> MetricDefinition:
        if definition.name in self._definitions:
            raise ValueError(f"Metric '{definition.name}' is already registered")

        self._definitions[definition.name] = definition
        self._samples[definition.name] = []
        return definition

    def record(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
        timestamp: Optional[str] = None,
    ) -> MetricSample:
        definition = self._definitions.get(metric_name)
        if definition is None:
            raise KeyError(f"Metric '{metric_name}' is not registered")

        labels = labels or {}
        unexpected = set(labels) - set(definition.labels)
        if unexpected:
            raise ValueError(f"Unexpected labels for '{metric_name}': {sorted(unexpected)}")

        sample = MetricSample(
            metric_name=metric_name,
            value=value,
            timestamp=timestamp or _utc_now_iso(),
            labels=labels,
        )
        self._samples[metric_name].append(sample)
        return sample

    def query(
        self,
        metric_name: str,
        labels: Optional[Dict[str, str]] = None,
    ) -> List[MetricSample]:
        if metric_name not in self._definitions:
            raise KeyError(f"Metric '{metric_name}' is not registered")

        samples = self._samples[metric_name]
        if not labels:
            return list(samples)

        return [
            sample for sample in samples
            if all(sample.labels.get(key) == value for key, value in labels.items())
        ]

    def list_metrics(self) -> List[MetricDefinition]:
        return list(self._definitions.values())

    def export_samples(self) -> Dict[str, List[MetricSample]]:
        return {name: list(samples) for name, samples in self._samples.items()}


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
