from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_metrics import (
        GovernanceIntegrityMetricsService,
    )
    from .deployment_governance_metrics_history import (
        GovernanceIntegrityMetricsSnapshot,
    )
    from .deployment_governance_metrics_retention import (
        GovernanceIntegrityMetricsRetentionService,
    )

DEFAULT_COLLECTION_INTERVAL_SECONDS = 60.0

DEFAULT_STOP_TIMEOUT_SECONDS = 5.0


class GovernanceIntegrityMetricsCollector:
    """
    Periodically captures governance audit notification delivery
    metrics history snapshots on a background thread, so capturing
    a snapshot never adds latency to a request or a delivery
    iteration.
    """

    def __init__(
        self,
        metrics_service: "GovernanceIntegrityMetricsService",
        *,
        interval_seconds: float = (
            DEFAULT_COLLECTION_INTERVAL_SECONDS
        ),
        retention_service: (
            "GovernanceIntegrityMetricsRetentionService | None"
        ) = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                "interval_seconds must be greater than zero"
            )

        self._metrics_service = metrics_service

        self._interval_seconds = interval_seconds

        self._retention_service = retention_service

        self._lock = threading.Lock()

        self._stop_event = threading.Event()

        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """
        Start the background collection thread. Raises RuntimeError
        if it is already running.
        """

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(
                    "metrics collector is already running"
                )

            self._stop_event.clear()

            self._thread = threading.Thread(
                target=self._run,
                name="governance-metrics-collector",
                daemon=True,
            )

            self._thread.start()

    def stop(
        self,
        *,
        timeout: float | None = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        """
        Signal the background collection thread to stop and wait for
        it to finish. A no-op if it is not running.
        """

        with self._lock:
            thread = self._thread

            if thread is None:
                return

            self._stop_event.set()

        thread.join(timeout=timeout)

        with self._lock:
            self._thread = None

    def is_running(self) -> bool:
        with self._lock:
            return (
                self._thread is not None
                and self._thread.is_alive()
            )

    def collect_once(
        self,
    ) -> "GovernanceIntegrityMetricsSnapshot | None":
        """
        Capture one metrics history snapshot immediately, outside of
        the background thread's own schedule.

        Returns None, and captures nothing, if there has been no
        dispatch activity at all: an all-zero snapshot would only
        add noise to the history, not a meaningful data point.

        When a snapshot is successfully captured and a retention
        service is configured, retention runs immediately
        afterward, so history never grows unbounded between
        collections.
        """

        metrics = self._metrics_service.snapshot()

        if metrics.total_dispatches == 0:
            return None

        snapshot = self._metrics_service.capture_snapshot()

        if self._retention_service is not None:
            self._retention_service.prune()

        return snapshot

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.collect_once()

            except Exception:
                # A transient failure to capture one snapshot must
                # not take down the background collection loop.
                pass

            if self._stop_event.wait(self._interval_seconds):
                break
