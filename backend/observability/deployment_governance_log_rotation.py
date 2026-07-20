from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_log_repository import (
        GovernanceLogRepository,
    )

DEFAULT_LOG_ROTATION_MAX_ENTRIES: int = 10000

DEFAULT_LOG_ROTATION_MAX_AGE_DAYS: int = 30

_UNSET = object()


@dataclass(frozen=True)
class GovernanceLogRotationPolicy:
    """
    The configured rotation rules a GovernanceLogRotationService
    enforces: how many entries to retain at most, and optionally how
    old an entry may get before it is discarded regardless of count.
    """

    max_entries: int

    max_age_days: int | None

    def __post_init__(self) -> None:
        if self.max_entries < 0:
            raise ValueError(
                "max_entries must not be negative"
            )

        if self.max_age_days is not None and self.max_age_days <= 0:
            raise ValueError(
                "max_age_days must be greater than zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "max_entries": self.max_entries,
            "max_age_days": self.max_age_days,
        }


class GovernanceLogRotationService:
    """
    Keeps a GovernanceLogRepository's history bounded under a
    configured GovernanceLogRotationPolicy.

    rotate() runs the full rotation cycle: entries older than
    max_age_days are discarded first (if age-based pruning is
    configured), then the remaining history is trimmed down to
    max_entries, oldest first. prune() runs only the count-based half
    of that cycle. Both are idempotent: running either again with no
    new entries appended in between discards nothing further, since
    the repository is already within policy.
    """

    def __init__(
        self,
        repository: "GovernanceLogRepository",
        *,
        policy: GovernanceLogRotationPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._policy = policy or GovernanceLogRotationPolicy(
            max_entries=DEFAULT_LOG_ROTATION_MAX_ENTRIES,
            max_age_days=DEFAULT_LOG_ROTATION_MAX_AGE_DAYS,
        )

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._lock = Lock()

    def policy(self) -> GovernanceLogRotationPolicy:
        """
        Return the currently configured rotation policy.
        """

        with self._lock:
            return self._policy

    def reconfigure(
        self,
        *,
        max_entries: Any = _UNSET,
        max_age_days: Any = _UNSET,
    ) -> None:
        """
        Replace the rotation policy without recreating the service.

        Only fields explicitly passed are changed (including None
        for max_age_days, to disable age-based pruning); omitted
        fields keep their current value. Takes effect on the next
        rotate()/prune() call.
        """

        with self._lock:
            self._policy = GovernanceLogRotationPolicy(
                max_entries=(
                    self._policy.max_entries
                    if max_entries is _UNSET
                    else max_entries
                ),
                max_age_days=(
                    self._policy.max_age_days
                    if max_age_days is _UNSET
                    else max_age_days
                ),
            )

    def prune(self) -> int:
        """
        Enforce only the count-based max_entries limit, discarding
        the oldest entries beyond it. Returns the number discarded.
        """

        policy = self.policy()

        return self._repository.prune(policy.max_entries)

    def rotate(self) -> int:
        """
        Run the full rotation cycle: age-based pruning (if
        configured) followed by count-based pruning. Returns the
        total number of entries discarded across both steps.
        """

        policy = self.policy()

        discarded = 0

        if policy.max_age_days is not None:
            cutoff = self._clock() - timedelta(
                days=policy.max_age_days
            )

            discarded += self._repository.prune_older_than(cutoff)

        discarded += self._repository.prune(policy.max_entries)

        return discarded
