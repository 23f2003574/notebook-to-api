from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_EXCLUDED_PATH_SUFFIXES = ("/health",)


@dataclass(frozen=True)
class GovernanceIntegrityRequestMetrics:
    """
    Aggregate HTTP request metrics collected for the governance API,
    as opposed to the notification delivery metrics tracked
    elsewhere in this package: this is about traffic to the
    governance API itself, not about outbound notification
    dispatches.
    """

    total_requests: int

    successful_requests: int

    failed_requests: int

    exceptions: int

    average_latency_ms: float

    def __post_init__(self) -> None:
        non_negative_fields = (
            self.total_requests,
            self.successful_requests,
            self.failed_requests,
            self.exceptions,
        )

        if any(value < 0 for value in non_negative_fields):
            raise ValueError(
                "governance request metrics counts must not be "
                "negative"
            )

        if (
            self.successful_requests + self.failed_requests
            != self.total_requests
        ):
            raise ValueError(
                "successful_requests + failed_requests must equal "
                "total_requests"
            )

        if self.average_latency_ms < 0:
            raise ValueError(
                "average_latency_ms must not be negative"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "exceptions": self.exceptions,
            "average_latency_ms": self.average_latency_ms,
        }


class GovernanceIntegrityRequestMetricsCollector:
    """
    Thread-safe running counters for governance API request
    metrics, updated by GovernanceIntegrityMetricsMiddleware.
    """

    def __init__(self) -> None:
        self._lock = Lock()

        self._successful_requests = 0

        self._failed_requests = 0

        self._exceptions = 0

        self._average_latency_ms = 0.0

    def record_request(
        self,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """
        Record one completed request and its outcome status.
        """

        if duration_ms < 0:
            raise ValueError(
                "duration_ms must not be negative"
            )

        with self._lock:
            if status_code < 400:
                self._successful_requests += 1

            else:
                self._failed_requests += 1

            self._record_latency_locked(duration_ms)

    def record_exception(self, duration_ms: float) -> None:
        """
        Record one request that failed with an unhandled exception
        rather than producing a response.
        """

        if duration_ms < 0:
            raise ValueError(
                "duration_ms must not be negative"
            )

        with self._lock:
            self._failed_requests += 1

            self._exceptions += 1

            self._record_latency_locked(duration_ms)

    def snapshot(self) -> GovernanceIntegrityRequestMetrics:
        with self._lock:
            return GovernanceIntegrityRequestMetrics(
                total_requests=(
                    self._successful_requests
                    + self._failed_requests
                ),
                successful_requests=self._successful_requests,
                failed_requests=self._failed_requests,
                exceptions=self._exceptions,
                average_latency_ms=self._average_latency_ms,
            )

    def reset(self) -> None:
        with self._lock:
            self._successful_requests = 0
            self._failed_requests = 0
            self._exceptions = 0
            self._average_latency_ms = 0.0

    def _record_latency_locked(self, duration_ms: float) -> None:
        total_requests = (
            self._successful_requests + self._failed_requests
        )

        self._average_latency_ms += (
            duration_ms - self._average_latency_ms
        ) / total_requests


def _is_excluded(request: Request) -> bool:
    path = request.url.path

    return any(
        path.endswith(suffix)
        for suffix in _EXCLUDED_PATH_SUFFIXES
    )


class GovernanceIntegrityMetricsMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that automatically collects request metrics
    (count, response status, latency, exceptions) for governance API
    endpoints.

    Health endpoints are excluded: they are polled frequently by
    infrastructure and would otherwise dominate the collected
    metrics without reflecting meaningful traffic. Latency is always
    recorded, including for excluded and failed requests, so timing
    data is never silently dropped. Exceptions raised downstream are
    recorded and re-raised rather than swallowed: this middleware
    observes failures, it does not handle them.
    """

    def __init__(
        self,
        app,
        *,
        collector: (
            GovernanceIntegrityRequestMetricsCollector | None
        ) = None,
    ) -> None:
        super().__init__(app)

        self.collector = (
            collector or GovernanceIntegrityRequestMetricsCollector()
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_excluded(request):
            return await call_next(request)

        started_at = time.monotonic()

        try:
            response = await call_next(request)

        except Exception:
            duration_ms = (time.monotonic() - started_at) * 1000.0

            self.record_exception(duration_ms)

            raise

        duration_ms = (time.monotonic() - started_at) * 1000.0

        self.record_request(response.status_code, duration_ms)

        return response

    def record_request(
        self,
        status_code: int,
        duration_ms: float,
    ) -> None:
        # A failure here must never take down the response path it
        # is only meant to observe.
        try:
            self.collector.record_request(status_code, duration_ms)

        except Exception:
            pass

    def record_exception(self, duration_ms: float) -> None:
        try:
            self.collector.record_exception(duration_ms)

        except Exception:
            pass
