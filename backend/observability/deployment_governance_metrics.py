from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_metrics_repository import (
        GovernanceIntegrityMetricsRepository,
    )


@dataclass(frozen=True)
class GovernanceIntegrityMetrics:
    """
    Point-in-time snapshot of live governance audit notification
    delivery activity: dispatch outcomes, retries, and average
    delivery duration.
    """

    total_dispatches: int

    successful_dispatches: int

    failed_dispatches: int

    retry_dispatches: int

    average_duration_ms: float

    def __post_init__(self) -> None:
        non_negative_fields = (
            self.total_dispatches,
            self.successful_dispatches,
            self.failed_dispatches,
            self.retry_dispatches,
        )

        if any(value < 0 for value in non_negative_fields):
            raise ValueError(
                "governance metrics counts must not be negative"
            )

        if (
            self.successful_dispatches + self.failed_dispatches
            != self.total_dispatches
        ):
            raise ValueError(
                "successful_dispatches + failed_dispatches must "
                "equal total_dispatches"
            )

        if self.average_duration_ms < 0:
            raise ValueError(
                "average_duration_ms must not be negative"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_dispatches": self.total_dispatches,
            "successful_dispatches": self.successful_dispatches,
            "failed_dispatches": self.failed_dispatches,
            "retry_dispatches": self.retry_dispatches,
            "average_duration_ms": self.average_duration_ms,
        }


class GovernanceIntegrityMetricsService:
    """
    Tracks live governance audit notification delivery metrics
    in-memory.

    Unlike GovernanceIntegrityExecutionMetricsService, which computes
    metrics by replaying stored execution history, this service is a
    running counter updated as delivery activity happens: recording
    is expected from concurrent delivery workers, so every mutating
    method is guarded by a single lock.
    """

    def __init__(
        self,
        repository: "GovernanceIntegrityMetricsRepository | None" = None,
        *,
        auto_flush_enabled: bool = True,
    ) -> None:
        self._lock = Lock()

        self._successful_dispatches = 0

        self._failed_dispatches = 0

        self._retry_dispatches = 0

        self._average_duration_ms = 0.0

        self._repository = repository

        self._auto_flush_enabled = auto_flush_enabled

    def record_success(self, duration_ms: float) -> None:
        """
        Record one successful provider delivery and its duration.
        """

        if duration_ms < 0:
            raise ValueError(
                "duration_ms must not be negative"
            )

        with self._lock:
            self._successful_dispatches += 1
            self._record_duration_locked(duration_ms)

        self.auto_flush()

    def record_failure(self, duration_ms: float) -> None:
        """
        Record one failed provider delivery and its duration.
        """

        if duration_ms < 0:
            raise ValueError(
                "duration_ms must not be negative"
            )

        with self._lock:
            self._failed_dispatches += 1
            self._record_duration_locked(duration_ms)

        self.auto_flush()

    def record_retry(self) -> None:
        """
        Record one dispatch being scheduled for retry.
        """

        with self._lock:
            self._retry_dispatches += 1

        self.auto_flush()

    def snapshot(self) -> GovernanceIntegrityMetrics:
        """
        Return an immutable snapshot of the metrics recorded so far.
        """

        with self._lock:
            return GovernanceIntegrityMetrics(
                total_dispatches=(
                    self._successful_dispatches
                    + self._failed_dispatches
                ),
                successful_dispatches=self._successful_dispatches,
                failed_dispatches=self._failed_dispatches,
                retry_dispatches=self._retry_dispatches,
                average_duration_ms=self._average_duration_ms,
            )

    def reset(self) -> None:
        """
        Clear every recorded metric back to zero, and clear the
        persisted snapshot from the repository, if one is
        configured.
        """

        with self._lock:
            self._successful_dispatches = 0
            self._failed_dispatches = 0
            self._retry_dispatches = 0
            self._average_duration_ms = 0.0

        if self._repository is not None:
            self._repository.reset()

    def load(self) -> None:
        """
        Replace the in-memory counters with whatever snapshot is
        persisted in the repository, if one is configured.

        Intended to be called once on startup. If the repository has
        nothing stored yet, the in-memory counters are left
        untouched.
        """

        if self._repository is None:
            return

        loaded = self._repository.load()

        if loaded is None:
            return

        with self._lock:
            self._successful_dispatches = loaded.successful_dispatches
            self._failed_dispatches = loaded.failed_dispatches
            self._retry_dispatches = loaded.retry_dispatches
            self._average_duration_ms = loaded.average_duration_ms

    def flush(self) -> None:
        """
        Persist the current in-memory metrics to the repository, if
        one is configured. A no-op otherwise.
        """

        if self._repository is None:
            return

        self._repository.save(self.snapshot())

    def auto_flush(self) -> None:
        """
        Flush to the repository, but only when auto-flush is enabled.

        Called after every mutating update (record_success,
        record_failure, record_retry) so a configured repository
        stays current without every caller having to remember to
        flush explicitly.
        """

        if self._auto_flush_enabled:
            self.flush()

    def _record_duration_locked(self, duration_ms: float) -> None:
        """
        Update the running average duration with one new sample.

        Must only be called while holding self._lock. Uses an
        incremental mean update rather than accumulating a running
        total, so precision does not degrade as the sample count
        grows.
        """

        total_dispatches = (
            self._successful_dispatches + self._failed_dispatches
        )

        self._average_duration_ms += (
            duration_ms - self._average_duration_ms
        ) / total_dispatches
