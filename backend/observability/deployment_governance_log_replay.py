from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_logging import GovernanceLogEntry
    from .deployment_governance_log_search import (
        GovernanceLogSearchService,
    )


@dataclass(frozen=True)
class GovernanceLogReplayCursor:
    """
    An immutable snapshot of a GovernanceLogReplayService's position
    within its (chronologically ordered) replay stream: how many
    entries have been consumed so far, and the timestamp of the
    entry currently sitting at that position (None once the cursor
    has reached the end of the stream).
    """

    position: int

    timestamp: datetime | None

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError(
                "position must not be negative"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "position": self.position,
            "timestamp": (
                None
                if self.timestamp is None
                else self.timestamp.isoformat()
            ),
        }


class GovernanceLogReplayService:
    """
    Replays a durable governance log stream chronologically (oldest
    first), for debugging and regression analysis: reconstructing
    the actual order of events in a past incident or comparing
    behavior across two runs.

    Read-only: nothing here ever mutates stored logs. The replay
    stream is a point-in-time snapshot taken on first use (via
    GovernanceLogSearchService.chronological, scoped to this
    service's since/event filters) and cached; entries appended to
    the repository afterward do not appear in an already-started
    replay. The replay cursor itself is immutable
    (GovernanceLogReplayCursor); this service holds the current
    cursor as internal, thread-safe mutable state and returns a
    fresh cursor value from seek()/reset() rather than ever mutating
    a shared one in place.
    """

    def __init__(
        self,
        search_service: "GovernanceLogSearchService",
        *,
        since: datetime | None = None,
        event: str | None = None,
    ) -> None:
        self._search_service = search_service

        self._since = since

        self._event = event

        self._lock = RLock()

        self._entries: tuple["GovernanceLogEntry", ...] | None = None

        self._position = 0

    def replay(
        self,
        *,
        limit: int | None = None,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return the full replay stream from the beginning,
        chronologically, optionally capped to the first limit
        entries. Does not read or move the cursor.
        """

        entries = self._snapshot()

        return entries if limit is None else entries[:limit]

    def next(
        self,
        *,
        limit: int = 1,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return up to limit entries starting at the current cursor
        position, and advance the cursor past them. Returns fewer
        (or zero) entries once the stream is exhausted.
        """

        if limit < 1:
            raise ValueError(
                "limit must be at least 1"
            )

        with self._lock:
            entries = self._snapshot_locked()

            start = self._position

            batch = entries[start : start + limit]

            self._position = start + len(batch)

            return batch

    def seek(
        self,
        *,
        timestamp: datetime | None = None,
        position: int | None = None,
    ) -> GovernanceLogReplayCursor:
        """
        Move the cursor to the first entry timestamped at or after
        timestamp, or directly to position (a raw index into the
        chronological stream). Exactly one of timestamp/position
        must be given. Returns the resulting cursor.
        """

        if (timestamp is None) == (position is None):
            raise ValueError(
                "exactly one of timestamp or position must be given"
            )

        with self._lock:
            entries = self._snapshot_locked()

            if position is not None:
                if position < 0:
                    raise ValueError(
                        "position must not be negative"
                    )

                self._position = min(position, len(entries))

            else:
                new_position = len(entries)

                for index, entry in enumerate(entries):
                    if entry.timestamp >= timestamp:
                        new_position = index

                        break

                self._position = new_position

            return self._cursor_locked()

    def reset(self) -> GovernanceLogReplayCursor:
        """
        Move the cursor back to the beginning of the replay stream.
        Returns the resulting cursor.
        """

        with self._lock:
            self._snapshot_locked()

            self._position = 0

            return self._cursor_locked()

    def cursor(self) -> GovernanceLogReplayCursor:
        """
        Return the current cursor without advancing it.
        """

        with self._lock:
            self._snapshot_locked()

            return self._cursor_locked()

    def _snapshot(self) -> tuple["GovernanceLogEntry", ...]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> tuple["GovernanceLogEntry", ...]:
        """
        Must only be called while holding self._lock.
        """

        if self._entries is None:
            self._entries = self._search_service.chronological(
                since=self._since, event=self._event
            )

        return self._entries

    def _cursor_locked(self) -> GovernanceLogReplayCursor:
        """
        Must only be called while holding self._lock, with
        self._entries already loaded.
        """

        entries = self._entries

        timestamp = (
            entries[self._position].timestamp
            if self._position < len(entries)
            else None
        )

        return GovernanceLogReplayCursor(
            position=self._position, timestamp=timestamp
        )
