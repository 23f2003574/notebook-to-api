from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Callable, Optional

from fastapi import APIRouter, Depends


class UnknownRequestError(KeyError):
    pass


@dataclass(frozen=True)
class GatewayMetric:
    """A single completed request's recorded metric."""

    request_id: str
    route: str
    method: str
    status_code: int
    latency_ms: float
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "route": self.route,
            "method": self.method,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class GatewayStatistics:
    """Aggregated, real-time gateway traffic statistics."""

    total_requests: int
    total_responses: int
    in_flight: int
    status_code_counts: dict = field(default_factory=dict)
    average_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    throughput_per_second: float = 0.0
    error_rate: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "total_responses": self.total_responses,
            "in_flight": self.in_flight,
            "status_code_counts": self.status_code_counts,
            "average_latency_ms": self.average_latency_ms,
            "min_latency_ms": self.min_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "throughput_per_second": self.throughput_per_second,
            "error_rate": self.error_rate,
        }


class GatewayAnalytics:
    """Records gateway request/response metrics and reports aggregated statistics."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._pending: dict = {}
        self._metrics: list = []
        self._first_request_at: Optional[float] = None
        self._request_counter = 0

    def record_request(self, route: str, method: str) -> str:
        with self._lock:
            self._request_counter += 1
            request_id = f"req-{self._request_counter}"
            now = self._clock()
            if self._first_request_at is None:
                self._first_request_at = now
            self._pending[request_id] = (route, method, now)
            return request_id

    def record_response(self, request_id: str, status_code: int) -> GatewayMetric:
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                raise UnknownRequestError(request_id)
            route, method, start = pending
            latency_ms = (self._clock() - start) * 1000
            metric = GatewayMetric(
                request_id=request_id,
                route=route,
                method=method,
                status_code=status_code,
                latency_ms=latency_ms,
                timestamp=datetime.now(timezone.utc),
            )
            self._metrics.append(metric)
            return metric

    def metrics(self) -> list:
        with self._lock:
            return list(self._metrics)

    def compute_statistics(self) -> GatewayStatistics:
        with self._lock:
            total_responses = len(self._metrics)
            status_code_counts: dict = {}
            for metric in self._metrics:
                key = str(metric.status_code)
                status_code_counts[key] = status_code_counts.get(key, 0) + 1

            if self._metrics:
                latencies = [metric.latency_ms for metric in self._metrics]
                average_latency_ms = sum(latencies) / len(latencies)
                min_latency_ms = min(latencies)
                max_latency_ms = max(latencies)
            else:
                average_latency_ms = 0.0
                min_latency_ms = 0.0
                max_latency_ms = 0.0

            if self._first_request_at is not None and total_responses > 0:
                elapsed = max(self._clock() - self._first_request_at, 1e-9)
                throughput_per_second = total_responses / elapsed
            else:
                throughput_per_second = 0.0

            if total_responses > 0:
                error_count = sum(
                    count for status, count in status_code_counts.items() if int(status) >= 400
                )
                error_rate = error_count / total_responses
            else:
                error_rate = 0.0

            return GatewayStatistics(
                total_requests=self._request_counter,
                total_responses=total_responses,
                in_flight=len(self._pending),
                status_code_counts=status_code_counts,
                average_latency_ms=average_latency_ms,
                min_latency_ms=min_latency_ms,
                max_latency_ms=max_latency_ms,
                throughput_per_second=throughput_per_second,
                error_rate=error_rate,
            )

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self._metrics.clear()
            self._first_request_at = None
            self._request_counter = 0


_gateway_analytics = GatewayAnalytics()


def get_gateway_analytics() -> GatewayAnalytics:
    return _gateway_analytics


router = APIRouter(prefix="/gateway/analytics", tags=["gateway-analytics"])


@router.get("")
def gateway_analytics_endpoint(analytics: GatewayAnalytics = Depends(get_gateway_analytics)) -> dict:
    return analytics.compute_statistics().to_dict()


@router.get("/metrics")
def gateway_metrics_endpoint(analytics: GatewayAnalytics = Depends(get_gateway_analytics)) -> list:
    return [metric.to_dict() for metric in analytics.metrics()]


@router.post("/reset")
def reset_gateway_analytics_endpoint(
    analytics: GatewayAnalytics = Depends(get_gateway_analytics),
) -> dict:
    analytics.reset()
    return {"status": "reset"}
