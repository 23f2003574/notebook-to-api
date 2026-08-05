from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4


VALID_SPAN_TYPES = ("http", "database", "inference", "pipeline", "worker")


@dataclass
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    baggage: Dict[str, str] = field(default_factory=dict)


@dataclass
class SpanRecord:
    span_id: str
    trace_id: str
    name: str
    span_type: str
    parent_span_id: Optional[str] = None
    start_time: str = ""
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    attributes: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.span_type not in VALID_SPAN_TYPES:
            raise ValueError(
                f"Unsupported span_type '{self.span_type}'. Expected one of {VALID_SPAN_TYPES}."
            )


class DistributedTracingEngine:
    def __init__(self):
        self._spans: Dict[str, SpanRecord] = {}
        self._traces: Dict[str, List[str]] = {}

    def start_span(
        self,
        name: str,
        span_type: str,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
    ) -> TraceContext:
        if parent_span_id is not None and parent_span_id not in self._spans:
            raise KeyError(f"Parent span '{parent_span_id}' not found")

        trace_id = trace_id or str(uuid4())
        span_id = str(uuid4())
        span = SpanRecord(
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            span_type=span_type,
            parent_span_id=parent_span_id,
            start_time=_utc_now_iso(),
            attributes=attributes or {},
        )
        self._spans[span_id] = span
        self._traces.setdefault(trace_id, []).append(span_id)
        return TraceContext(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )

    def finish_span(self, span_id: str) -> SpanRecord:
        span = self._spans.get(span_id)
        if span is None:
            raise KeyError(f"Span '{span_id}' not found")
        if span.end_time is not None:
            raise ValueError(f"Span '{span_id}' is already finished")

        span.end_time = _utc_now_iso()
        span.duration_ms = _duration_ms(span.start_time, span.end_time)
        return span

    def link(self, parent_span_id: str, child_span_id: str) -> SpanRecord:
        if parent_span_id not in self._spans:
            raise KeyError(f"Span '{parent_span_id}' not found")

        child = self._spans.get(child_span_id)
        if child is None:
            raise KeyError(f"Span '{child_span_id}' not found")

        child.parent_span_id = parent_span_id
        return child

    def trace(self, trace_id: str) -> List[SpanRecord]:
        span_ids = self._traces.get(trace_id, [])
        return [self._spans[span_id] for span_id in span_ids]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(start_iso: str, end_iso: str) -> float:
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    return (end - start).total_seconds() * 1000
