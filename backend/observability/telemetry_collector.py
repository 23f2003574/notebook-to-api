from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

from backend.observability.metrics_registry import MetricsRegistry


VALID_SOURCES = ("metrics", "events", "logs", "traces")


@dataclass
class TelemetryRecord:
    source: str
    event_type: str
    payload: Dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"Unsupported source '{self.source}'. Expected one of {VALID_SOURCES}."
            )
        if not self.timestamp:
            self.timestamp = _utc_now_iso()


@dataclass
class CollectionBatch:
    batch_id: str
    records: List[TelemetryRecord] = field(default_factory=list)


class TelemetryCollector:
    def __init__(self):
        self._buffer: List[TelemetryRecord] = []

    def collect(
        self,
        source: str,
        event_type: str,
        payload: Optional[Dict] = None,
        timestamp: Optional[str] = None,
    ) -> TelemetryRecord:
        record = TelemetryRecord(
            source=source,
            event_type=event_type,
            payload=payload or {},
            timestamp=timestamp or "",
        )
        self._buffer.append(record)
        return record

    def ingest(self, records: Iterable[TelemetryRecord]) -> CollectionBatch:
        batch = CollectionBatch(batch_id=str(uuid4()))
        for record in records:
            self._buffer.append(record)
            batch.records.append(record)
        return batch

    def flush(self) -> CollectionBatch:
        batch = CollectionBatch(batch_id=str(uuid4()), records=list(self._buffer))
        self._buffer.clear()
        return batch

    def snapshot(self) -> List[TelemetryRecord]:
        return list(self._buffer)

    def collect_metrics(self, registry: MetricsRegistry) -> List[TelemetryRecord]:
        collected = []
        for name, samples in registry.export_samples().items():
            for sample in samples:
                collected.append(
                    self.collect(
                        source="metrics",
                        event_type=name,
                        payload={"value": sample.value, "labels": sample.labels},
                        timestamp=sample.timestamp,
                    )
                )
        return collected


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
