from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_metrics_history import (
        GovernanceIntegrityMetricsHistoryRepository,
        GovernanceIntegrityMetricsSnapshot,
    )

DEFAULT_RETENTION_MAX_AGE: timedelta = timedelta(days=30)

DEFAULT_RETENTION_MAX_ENTRIES: int = 500

_UNSET = object()


@dataclass(frozen=True)
class GovernanceIntegrityMetricsRetentionPolicy:
    """
    The configured retention rules a
    GovernanceIntegrityMetricsRetentionService enforces. Either rule
    may be disabled (None); when both are set, a snapshot must
    satisfy both to be retained.
    """

    max_age: timedelta | None

    max_entries: int | None

    def __post_init__(self) -> None:
        if self.max_age is not None and self.max_age <= timedelta(0):
            raise ValueError(
                "max_age must be greater than zero"
            )

        if self.max_entries is not None and self.max_entries < 0:
            raise ValueError(
                "max_entries must not be negative"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "max_age_seconds": (
                None
                if self.max_age is None
                else self.max_age.total_seconds()
            ),
            "max_entries": self.max_entries,
        }


class GovernanceIntegrityMetricsRetentionService:
    """
    Expires old governance metrics history snapshots to keep storage
    growth bounded, retaining the newest snapshots under a
    configurable max age and/or max entry count.
    """

    def __init__(
        self,
        history_repository: (
            "GovernanceIntegrityMetricsHistoryRepository"
        ),
        *,
        max_age: timedelta | None = DEFAULT_RETENTION_MAX_AGE,
        max_entries: int | None = DEFAULT_RETENTION_MAX_ENTRIES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Validate through the policy dataclass so both this
        # service's config and retention_policy()'s output are
        # always guaranteed consistent with the same rules.
        self._policy = GovernanceIntegrityMetricsRetentionPolicy(
            max_age=max_age, max_entries=max_entries
        )

        self._history_repository = history_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._lock = Lock()

    def retention_policy(
        self,
    ) -> GovernanceIntegrityMetricsRetentionPolicy:
        """
        Return the currently configured retention policy.
        """

        with self._lock:
            return self._policy

    def reconfigure(
        self,
        *,
        max_age: Any = _UNSET,
        max_entries: Any = _UNSET,
    ) -> None:
        """
        Replace the retention policy without recreating the service.

        Only fields explicitly passed are changed (including None,
        to disable that rule); omitted fields keep their current
        value. Takes effect on the next expired()/prune() call.
        """

        with self._lock:
            current = self._policy

            new_max_age = (
                current.max_age if max_age is _UNSET else max_age
            )

            new_max_entries = (
                current.max_entries
                if max_entries is _UNSET
                else max_entries
            )

            self._policy = GovernanceIntegrityMetricsRetentionPolicy(
                max_age=new_max_age, max_entries=new_max_entries
            )

    def expired(
        self,
    ) -> tuple["GovernanceIntegrityMetricsSnapshot", ...]:
        """
        Return every snapshot that is currently expired under the
        configured policy, without deleting anything, oldest first.
        """

        newest_first = self._history_repository.list()

        retain_count = self._retain_count(newest_first)

        # Snapshots beyond retain_count are the expired ones; reverse
        # them to read oldest first, in true chronological order.
        return tuple(reversed(newest_first[retain_count:]))

    def prune(self) -> int:
        """
        Delete every currently expired snapshot from the repository.
        Returns the number of snapshots removed.
        """

        newest_first = self._history_repository.list()

        retain_count = self._retain_count(newest_first)

        return self._history_repository.prune(retain_count)

    def _retain_count(
        self,
        newest_first: tuple["GovernanceIntegrityMetricsSnapshot", ...],
    ) -> int:
        with self._lock:
            policy = self._policy

        retain_count = len(newest_first)

        if policy.max_age is not None:
            cutoff = self._clock() - policy.max_age

            retain_count = min(
                retain_count,
                sum(
                    1
                    for snapshot in newest_first
                    if snapshot.captured_at >= cutoff
                ),
            )

        if policy.max_entries is not None:
            retain_count = min(retain_count, policy.max_entries)

        return retain_count
