from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class GovernanceLivenessStatus:
    """
    A point-in-time snapshot of whether the governance runtime
    process is alive, and for how long it has been running.
    """

    alive: bool

    checked_at: datetime

    uptime_seconds: int

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError(
                "checked_at must be timezone-aware"
            )

        if self.uptime_seconds < 0:
            raise ValueError(
                "uptime_seconds must be >= 0"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "alive": self.alive,
            "checked_at": self.checked_at.isoformat(),
            "uptime_seconds": self.uptime_seconds,
        }


class GovernanceLivenessService:
    """
    Tracks whether the governance runtime process itself is up.

    Unlike GovernanceHealthService and GovernanceReadinessService,
    this performs no dependency validation whatsoever: it does not
    check any component, subsystem, or registry. It only answers
    "has this process been started, and for how long", which by
    definition can only be true of the process currently executing
    this code.

    Uptime is measured against a monotonic clock (time.monotonic by
    default) rather than wall-clock time, so it cannot go backwards
    or jump if the system clock is adjusted. checked_at on the
    returned status is still wall-clock (UTC), since that is what a
    caller needs to correlate a liveness check against other events.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._monotonic = monotonic or time.monotonic

        self._started_at: float | None = None

    def start(self) -> None:
        """
        Mark the service as started, recording the current monotonic
        time as the uptime epoch.

        Idempotent: calling start() again while already started is a
        no-op, so uptime keeps counting from the original start
        rather than being pushed forward. Call reset() first to
        re-arm from zero.
        """

        if self._started_at is None:
            self._started_at = self._monotonic()

    def check(self) -> GovernanceLivenessStatus:
        """
        Return the current liveness status: alive if start() has run
        since the last reset(), plus the elapsed uptime.
        """

        return GovernanceLivenessStatus(
            alive=self._started_at is not None,
            checked_at=self._clock(),
            uptime_seconds=self.uptime(),
        )

    def uptime(self) -> int:
        """
        Return the number of whole seconds since start(), or 0 if
        the service has not been started (or has been reset).
        """

        if self._started_at is None:
            return 0

        return int(self._monotonic() - self._started_at)

    def reset(self) -> None:
        """
        Clear started state, as on shutdown. After reset(), check()
        reports alive=False and zero uptime until start() is called
        again.

        Safe to call whether or not the service was ever started.
        """

        self._started_at = None


# Shared for the lifetime of the process: liveness answers "is this
# process alive", which is inherently process-wide state rather than
# something that can be meaningfully rebuilt fresh per request, the
# way the health and readiness services are.
_liveness_service = GovernanceLivenessService()


def get_liveness_service() -> GovernanceLivenessService:
    """
    Return the process-wide governance liveness service, starting it
    on first access if nothing has started it yet (e.g. a bare API
    server process with no delivery runtime driving it explicitly).

    Safe to call repeatedly: start() is a no-op once already started.
    """

    _liveness_service.start()

    return _liveness_service
