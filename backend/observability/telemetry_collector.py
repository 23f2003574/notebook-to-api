from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

from backend.observability.distributed_tracing import DistributedTracingEngine
from backend.observability.log_aggregation import LogAggregationService
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

    def collect_traces(
        self, tracing_engine: DistributedTracingEngine, trace_id: str
    ) -> List[TelemetryRecord]:
        collected = []
        for span in tracing_engine.trace(trace_id):
            collected.append(
                self.collect(
                    source="traces",
                    event_type=span.name,
                    payload={
                        "span_id": span.span_id,
                        "trace_id": span.trace_id,
                        "parent_span_id": span.parent_span_id,
                        "span_type": span.span_type,
                        "duration_ms": span.duration_ms,
                    },
                    timestamp=span.start_time,
                )
            )
        return collected

    def collect_logs(self, log_service: LogAggregationService) -> List[TelemetryRecord]:
        collected = []
        for entry in log_service.query():
            collected.append(
                self.collect(
                    source="logs",
                    event_type=entry.severity,
                    payload={
                        "source": entry.source,
                        "message": entry.message,
                        "attributes": entry.attributes,
                    },
                    timestamp=entry.timestamp,
                )
            )
        return collected


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
