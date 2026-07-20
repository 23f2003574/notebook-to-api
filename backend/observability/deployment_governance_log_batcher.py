from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_logging import GovernanceLogEntry
    from .deployment_governance_log_repository import (
        GovernanceLogRepository,
    )

DEFAULT_BATCH_SIZE: int = 100

DEFAULT_FLUSH_INTERVAL_SECONDS: float = 5.0


@dataclass(frozen=True)
class GovernanceLogBatch:
    """
    One flushed batch of log entries, in the order they were
    enqueued.
    """

    entries: tuple["GovernanceLogEntry", ...]

    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )


class GovernanceLogBatcher:
    """
    Buffers log entries in memory and writes them to a
    GovernanceLogRepository in batches, to reduce repository I/O
    under high log volume (one transaction per batch under the
    SQLite backend, rather than one per entry).

    Entries are written in the order they were enqueued
    (append_many() preserves order end to end). A batch is flushed
    automatically once it reaches batch_size, or once
    flush_interval_seconds have elapsed since the last flush,
    whichever comes first -- flush_if_needed() checks both
    conditions and is meant to be called both after every enqueue
    (to catch the size threshold immediately) and on the runtime's
    own periodic cadence (to catch the interval threshold even
    during quiet periods with no new entries). flush() flushes
    unconditionally and is meant to be called during shutdown, so no
    enqueued entry is ever lost to a batch that never reached its
    threshold.
    """

    def __init__(
        self,
        repository: "GovernanceLogRepository",
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_seconds: float = (
            DEFAULT_FLUSH_INTERVAL_SECONDS
        ),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError(
                "batch_size must be at least 1"
            )

        if flush_interval_seconds <= 0:
            raise ValueError(
                "flush_interval_seconds must be greater than zero"
            )

        self._repository = repository

        self._batch_size = batch_size

        self._flush_interval_seconds = flush_interval_seconds

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._lock = Lock()

        self._pending: list["GovernanceLogEntry"] = []

        self._last_flush_at = self._clock()

    def enqueue(self, entry: "GovernanceLogEntry") -> None:
        """
        Add entry to the pending batch. Does not itself flush; call
        flush_if_needed() (or flush()) to actually write it out.
        """

        with self._lock:
            self._pending.append(entry)

    def pending_count(self) -> int:
        """
        Return how many entries are currently buffered, not yet
        written to the repository.
        """

        with self._lock:
            return len(self._pending)

    def flush(self) -> GovernanceLogBatch | None:
        """
        Write every currently pending entry to the repository in one
        call, in enqueue order, and clear the pending queue,
        regardless of whether batch_size or flush_interval_seconds
        has been reached. Returns the flushed GovernanceLogBatch, or
        None if nothing was pending.
        """

        with self._lock:
            if not self._pending:
                self._last_flush_at = self._clock()

                return None

            entries = tuple(self._pending)

            self._pending = []

            flushed_at = self._clock()

            self._last_flush_at = flushed_at

        self._repository.append_many(entries)

        return GovernanceLogBatch(
            entries=entries, created_at=flushed_at
        )

    def flush_if_needed(self) -> GovernanceLogBatch | None:
        """
        Flush only if the pending queue has reached batch_size, or
        flush_interval_seconds have elapsed since the last flush.
        A no-op (returns None) otherwise, including when nothing is
        pending.
        """

        with self._lock:
            if not self._pending:
                return None

            size_triggered = len(self._pending) >= self._batch_size

            elapsed_seconds = (
                self._clock() - self._last_flush_at
            ).total_seconds()

            interval_triggered = (
                elapsed_seconds >= self._flush_interval_seconds
            )

            should_flush = size_triggered or interval_triggered

        if not should_flush:
            return None

        return self.flush()
