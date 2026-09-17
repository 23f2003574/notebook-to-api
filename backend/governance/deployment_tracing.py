from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Mapping, Optional

from fastapi import APIRouter, HTTPException

from .deployment_metrics import (
    DEPLOY_COUNT,
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    SUCCESS_COUNT,
    DeploymentMetricsCollector,
)

SPAN_STATUSES = ("OK", "ERROR")


def _new_id() -> str:
    return uuid.uuid4().hex


def _normalize_status(status: str) -> str:
    status = status.upper()
    if status not in SPAN_STATUSES:
        raise ValueError(
            f"invalid span status {status!r}; expected one of {SPAN_STATUSES}"
        )
    return status


@dataclass(frozen=True)
class Span:
    """One immutable span within a deployment trace."""

    span_id: str
    trace_id: str
    parent_span_id: Optional[str]
    operation: str
    start_time: datetime
    end_time: Optional[datetime] = None
    status: Optional[str] = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() * 1000

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "operation": self.operation,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": dict(self.attributes),
        }


class UnknownTraceError(KeyError):
    pass


class UnknownSpanError(KeyError):
    pass


class DeploymentTracingService:
    """Propagates end-to-end traces across deployment operations."""

    def __init__(self) -> None:
        self._spans: dict[str, dict[str, Span]] = {}
        self._lock = Lock()

    def start_trace(
        self,
        operation: str,
        *,
        attributes: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[datetime] = None,
        metrics_collector: Optional[DeploymentMetricsCollector] = None,
    ) -> Span:
        trace_id = _new_id()
        with self._lock:
            self._spans[trace_id] = {}
        span = self.create_span(
            trace_id,
            operation,
            attributes=attributes,
            timestamp=timestamp,
        )
        if metrics_collector is not None:
            metrics_collector.increment(DEPLOY_COUNT)
        return span

    def create_span(
        self,
        trace_id: str,
        operation: str,
        *,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> Span:
        span = Span(
            span_id=_new_id(),
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            operation=operation,
            start_time=timestamp or datetime.now(timezone.utc),
            attributes=dict(attributes or {}),
        )
        with self._lock:
            spans = self._spans.setdefault(trace_id, {})
            if parent_span_id is not None and parent_span_id not in spans:
                raise UnknownSpanError(parent_span_id)
            spans[span.span_id] = span
        return span

    def finish_span(
        self,
        trace_id: str,
        span_id: str,
        *,
        status: str = "OK",
        timestamp: Optional[datetime] = None,
        metrics_collector: Optional[DeploymentMetricsCollector] = None,
    ) -> Span:
        with self._lock:
            spans = self._spans.get(trace_id)
            if spans is None:
                raise UnknownTraceError(trace_id)
            span = spans.get(span_id)
            if span is None:
                raise UnknownSpanError(span_id)
            finished = Span(
                span_id=span.span_id,
                trace_id=span.trace_id,
                parent_span_id=span.parent_span_id,
                operation=span.operation,
                start_time=span.start_time,
                end_time=timestamp or datetime.now(timezone.utc),
                status=_normalize_status(status),
                attributes=span.attributes,
            )
            spans[span_id] = finished
        if metrics_collector is not None and finished.parent_span_id is None:
            metrics_collector.observe(DEPLOY_DURATION_MS, finished.duration_ms)
            metrics_collector.increment(
                SUCCESS_COUNT if finished.status == "OK" else FAILURE_COUNT
            )
        return finished

    def get_trace(self, trace_id: str) -> list[Span]:
        with self._lock:
            spans = self._spans.get(trace_id)
            if spans is None:
                raise UnknownTraceError(trace_id)
            return sorted(spans.values(), key=lambda s: s.start_time)

    def list_traces(self) -> list[str]:
        with self._lock:
            return list(self._spans.keys())


_service = DeploymentTracingService()


def get_deployment_tracing_service() -> DeploymentTracingService:
    return _service


router = APIRouter(prefix="/governance", tags=["governance-traces"])


@router.get("/traces")
def list_traces() -> list[str]:
    return get_deployment_tracing_service().list_traces()


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str) -> list[dict]:
    try:
        spans = get_deployment_tracing_service().get_trace(trace_id)
    except UnknownTraceError:
        raise HTTPException(status_code=404, detail="trace not found")
    return [span.to_dict() for span in spans]
