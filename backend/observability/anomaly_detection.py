from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Dict, List, Optional
from uuid import uuid4


VALID_DETECTION_TYPES = ("threshold", "statistical", "trend_drift", "seasonal")


@dataclass
class DetectionBaseline:
    metric_name: str
    detection_type: str
    mean: float
    stddev: float
    sensitivity: float = 3.0

    def __post_init__(self):
        if self.detection_type not in VALID_DETECTION_TYPES:
            raise ValueError(
                f"Unsupported detection_type '{self.detection_type}'. "
                f"Expected one of {VALID_DETECTION_TYPES}."
            )


@dataclass
class AnomalyEvent:
    event_id: str
    metric_name: str
    value: float
    score: float
    detected_at: str
    is_false_positive: bool = False


class AnomalyDetectionEngine:
    def __init__(self):
        self._baselines: Dict[str, DetectionBaseline] = {}
        self._events: Dict[str, AnomalyEvent] = {}

    def train_baseline(
        self,
        metric_name: str,
        values: List[float],
        detection_type: str = "statistical",
        sensitivity: float = 3.0,
    ) -> DetectionBaseline:
        if not values:
            raise ValueError("Cannot train a baseline from an empty sample set")

        baseline = DetectionBaseline(
            metric_name=metric_name,
            detection_type=detection_type,
            mean=mean(values),
            stddev=pstdev(values) if len(values) > 1 else 0.0,
            sensitivity=sensitivity,
        )
        self._baselines[metric_name] = baseline
        return baseline

    def score(self, metric_name: str, value: float) -> float:
        baseline = self._baselines.get(metric_name)
        if baseline is None:
            raise KeyError(f"No baseline trained for '{metric_name}'")

        if baseline.stddev == 0:
            return 0.0 if value == baseline.mean else float("inf")

        return abs(value - baseline.mean) / baseline.stddev

    def detect(
        self,
        metric_name: str,
        value: float,
        timestamp: Optional[str] = None,
    ) -> Optional[AnomalyEvent]:
        baseline = self._baselines.get(metric_name)
        if baseline is None:
            raise KeyError(f"No baseline trained for '{metric_name}'")

        deviation_score = self.score(metric_name, value)
        if deviation_score < baseline.sensitivity:
            return None

        event = AnomalyEvent(
            event_id=str(uuid4()),
            metric_name=metric_name,
            value=value,
            score=deviation_score,
            detected_at=timestamp or _utc_now_iso(),
        )
        self._events[event.event_id] = event
        return event

    def feedback(self, event_id: str, is_false_positive: bool) -> AnomalyEvent:
        event = self._events.get(event_id)
        if event is None:
            raise KeyError(f"Anomaly event '{event_id}' not found")

        event.is_false_positive = is_false_positive
        return event

    def list_events(self, exclude_false_positives: bool = False) -> List[AnomalyEvent]:
        events = list(self._events.values())
        if exclude_false_positives:
            events = [event for event in events if not event.is_false_positive]
        return events


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
