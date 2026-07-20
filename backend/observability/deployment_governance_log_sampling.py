from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from threading import Lock
from typing import Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_logging import GovernanceLogEntry

_ALWAYS_LOGGED_LEVELS = ("ERROR", "CRITICAL")

DEFAULT_SAMPLING_RATE: float = 1.0


def _validate_rate(rate: float, *, label: str) -> None:
    if not (0.0 <= rate <= 1.0):
        raise ValueError(
            f"{label} must be between 0 and 1"
        )


@dataclass(frozen=True)
class GovernanceLogSamplingPolicy:
    """
    The configured sampling rules a GovernanceLogSamplingService
    enforces: what fraction of entries to keep by default, optional
    per-level overrides, and event names that always bypass
    sampling regardless of rate.
    """

    default_rate: float = DEFAULT_SAMPLING_RATE

    per_level: Mapping[str, float] = field(default_factory=dict)

    always_log_events: frozenset[str] = field(
        default_factory=frozenset
    )

    def __post_init__(self) -> None:
        _validate_rate(self.default_rate, label="default_rate")

        for level, rate in self.per_level.items():
            _validate_rate(
                rate, label=f"per_level rate for '{level}'"
            )

    def rate_for_level(self, level: str) -> float:
        return self.per_level.get(level, self.default_rate)

    def to_dict(self) -> dict[str, object]:
        return {
            "default_rate": self.default_rate,
            "per_level": dict(self.per_level),
            "always_log_events": sorted(self.always_log_events),
        }


class GovernanceLogSamplingService:
    """
    Decides which log entries are worth persisting durably, to keep
    high-volume, low-value events (e.g. frequent success events)
    from overwhelming the log repository while never dropping
    anything actually important.

    Sampling is deterministic: the same event name always gets the
    same keep/drop decision at a given rate (computed from a stable
    hash of the event name, not randomly per call), so the same
    high-frequency event is either always sampled in or always
    sampled out rather than flickering between runs. ERROR (and
    CRITICAL, for forward compatibility with loggers that use it)
    entries always bypass sampling, as does any event explicitly
    listed in the policy's always_log_events, regardless of the
    configured rate.
    """

    def __init__(
        self,
        policy: GovernanceLogSamplingPolicy | None = None,
    ) -> None:
        self._lock = Lock()

        self._policy = policy or GovernanceLogSamplingPolicy()

    def policy(self) -> GovernanceLogSamplingPolicy:
        """
        Return the currently configured sampling policy.
        """

        with self._lock:
            return self._policy

    def update_policy(
        self, policy: GovernanceLogSamplingPolicy
    ) -> None:
        """
        Replace the sampling policy outright, without recreating the
        service. Takes effect on the next should_log() call.
        """

        with self._lock:
            self._policy = policy

    def should_log(self, entry: "GovernanceLogEntry") -> bool:
        """
        Return whether entry should be persisted durably.

        Always True for ERROR/CRITICAL entries and for events listed
        in the policy's always_log_events. Otherwise, deterministic
        on the configured rate for entry.level (or default_rate if
        no per-level override is configured) and a stable hash of
        entry.event.
        """

        if entry.level in _ALWAYS_LOGGED_LEVELS:
            return True

        policy = self.policy()

        if entry.event in policy.always_log_events:
            return True

        rate = policy.rate_for_level(entry.level)

        return _stable_unit_interval(entry.event) < rate


def _stable_unit_interval(value: str) -> float:
    """
    Deterministically map value to a float in [0, 1), stable across
    processes and Python versions (unlike the builtin hash(), which
    is randomized per process for strings unless PYTHONHASHSEED is
    fixed).
    """

    digest = hashlib.sha256(value.encode("utf-8")).digest()

    as_int = int.from_bytes(digest[:8], byteorder="big")

    return as_int / 2**64
