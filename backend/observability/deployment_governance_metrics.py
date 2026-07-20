from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_metrics_repository import (
        GovernanceIntegrityMetricsRepository,
    )
    from .deployment_governance_metrics_history import (
        GovernanceIntegrityMetricsHistoryRepository,
        GovernanceIntegrityMetricsSnapshot,
    )
    from .deployment_governance_metrics_export import (
        GovernanceIntegrityMetricsExportService,
    )
    from .deployment_governance_logging import (
        GovernanceIntegrityLogger,
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
        history_repository: (
            "GovernanceIntegrityMetricsHistoryRepository | None"
        ) = None,
        history_retention: int | None = None,
        logger: "GovernanceIntegrityLogger | None" = None,
    ) -> None:
        self._lock = Lock()

        self._successful_dispatches = 0

        self._failed_dispatches = 0

        self._retry_dispatches = 0

        self._average_duration_ms = 0.0

        self._repository = repository

        self._auto_flush_enabled = auto_flush_enabled

        self._history_repository = history_repository

        self._history_retention = history_retention

        self._logger = logger

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

        if self._logger is not None:
            self._logger.info(
                "metrics", "record_success", duration_ms=duration_ms
            )

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

        if self._logger is not None:
            self._logger.warning(
                "metrics", "record_failure", duration_ms=duration_ms
            )

        self.auto_flush()

    def record_retry(self) -> None:
        """
        Record one dispatch being scheduled for retry.
        """

        with self._lock:
            self._retry_dispatches += 1

        if self._logger is not None:
            self._logger.info("metrics", "record_retry")

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
        one is configured, and capture a new history snapshot, if a
        history repository is configured. A no-op otherwise.
        """

        if self._repository is not None:
            self._repository.save(self.snapshot())

        self.capture_snapshot()

    def capture_snapshot(
        self,
    ) -> "GovernanceIntegrityMetricsSnapshot | None":
        """
        Append the current metrics as a new immutable history entry,
        if a history repository is configured.

        Returns the captured snapshot, or None if no history
        repository is configured. When a retention limit is
        configured, prunes the history down to that limit right
        after appending.
        """

        if self._history_repository is None:
            return None

        from .deployment_governance_metrics_history import (
            GovernanceIntegrityMetricsSnapshot,
        )

        snapshot = GovernanceIntegrityMetricsSnapshot(
            captured_at=datetime.now(timezone.utc),
            metrics=self.snapshot(),
        )

        self._history_repository.append(snapshot)

        if self._history_retention is not None:
            self._history_repository.prune(self._history_retention)

        return snapshot

    def history(
        self,
        limit: int | None = None,
    ) -> tuple["GovernanceIntegrityMetricsSnapshot", ...]:
        """
        Return captured metrics snapshots newest first, or an empty
        tuple if no history repository is configured.
        """

        if self._history_repository is None:
            return ()

        return self._history_repository.list(limit)

    def latest(self) -> "GovernanceIntegrityMetricsSnapshot | None":
        """
        Return the most recently captured metrics snapshot, or None
        if no history repository is configured or nothing has been
        captured yet.
        """

        if self._history_repository is None:
            return None

        return self._history_repository.latest()

    def prune(self, max_entries: int) -> int:
        """
        Discard the oldest metrics snapshots beyond max_entries.
        Returns the number discarded, or 0 if no history repository
        is configured.
        """

        if self._history_repository is None:
            return 0

        return self._history_repository.prune(max_entries)

    def export_service(self) -> "GovernanceIntegrityMetricsExportService":
        """
        Return a GovernanceIntegrityMetricsExportService bound to
        this service, for formatting current metrics (and optionally
        history) for offline analysis.
        """

        from .deployment_governance_metrics_export import (
            GovernanceIntegrityMetricsExportService,
        )

        return GovernanceIntegrityMetricsExportService(self)

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

    def set_auto_flush_enabled(self, enabled: bool) -> None:
        """
        Enable or disable auto-flush after every mutating update,
        without recreating the service.
        """

        with self._lock:
            self._auto_flush_enabled = enabled

    def set_logger(
        self, logger: "GovernanceIntegrityLogger | None"
    ) -> None:
        """
        Attach (or detach) a GovernanceIntegrityLogger after
        construction, without recreating the service.
        """

        with self._lock:
            self._logger = logger

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
