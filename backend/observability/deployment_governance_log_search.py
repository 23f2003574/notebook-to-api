from __future__ import annotations

from datetime import datetime
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_logging import GovernanceLogEntry
    from .deployment_governance_log_repository import (
        GovernanceLogRepository,
    )

DEFAULT_SEARCH_ITERATION_BATCH_SIZE = 500


class GovernanceLogSearchService:
    """
    Read-only indexed search over a GovernanceLogRepository's
    durable log history, for debugging and audit review.

    Every method returns entries newest first and delegates to the
    repository's own search()/count(), which is index-backed (on
    timestamp, level, component, and event) under the SQLite
    backend.
    """

    def __init__(
        self,
        repository: "GovernanceLogRepository",
    ) -> None:
        self._repository = repository

    def search(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        event: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return log entries newest first, matching every given filter
        (filters combine with AND). since/until form an inclusive
        time range: an entry timestamped exactly at since or until
        is included. limit/offset paginate the newest-first result
        set.
        """

        return self._repository.search(
            level=level,
            component=component,
            event=event,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    def count(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        event: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """
        Return how many entries match the given filters, ignoring
        pagination -- useful for computing total pages.
        """

        return self._repository.count(
            level=level,
            component=component,
            event=event,
            since=since,
            until=until,
        )

    def by_level(
        self,
        level: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return entries at exactly one level, newest first.
        """

        return self.search(level=level, limit=limit, offset=offset)

    def by_component(
        self,
        component: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return entries from exactly one component, newest first.
        """

        return self.search(
            component=component, limit=limit, offset=offset
        )

    def by_event(
        self,
        event: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return entries matching exactly one event name, newest
        first.
        """

        return self.search(event=event, limit=limit, offset=offset)

    def between(
        self,
        since: datetime,
        until: datetime,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple["GovernanceLogEntry", ...]:
        """
        Return entries within an inclusive time range
        [since, until], newest first.
        """

        return self.search(
            since=since, until=until, limit=limit, offset=offset
        )

    def iter_search(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        event: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        batch_size: int = DEFAULT_SEARCH_ITERATION_BATCH_SIZE,
    ) -> Iterator["GovernanceLogEntry"]:
        """
        Yield every matching entry newest first, fetching batch_size
        entries at a time instead of materializing the full result
        set in memory. Intended for large exports.

        Like offset-based pagination generally, this can skip or
        repeat entries if the repository is written to concurrently
        while iterating; it is meant for point-in-time exports of an
        otherwise-quiescent history, not a live tail.
        """

        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        offset = 0

        while True:
            batch = self.search(
                level=level,
                component=component,
                event=event,
                since=since,
                until=until,
                limit=batch_size,
                offset=offset,
            )

            if not batch:
                return

            yield from batch

            if len(batch) < batch_size:
                return

            offset += batch_size
