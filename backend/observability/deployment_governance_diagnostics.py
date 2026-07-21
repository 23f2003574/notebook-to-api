from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class GovernanceDiagnostics:
    """
    A point-in-time, read-only snapshot of governance runtime state,
    intended for debugging rather than pass/fail judgments (that is
    what GovernanceHealthService and GovernanceReadinessService are
    for).
    """

    generated_at: datetime

    runtime_state: str

    active_dispatches: int

    registered_providers: int

    pending_dispatches: int

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError(
                "generated_at must be timezone-aware"
            )

        if self.active_dispatches < 0:
            raise ValueError(
                "active_dispatches must be >= 0"
            )

        if self.registered_providers < 0:
            raise ValueError(
                "registered_providers must be >= 0"
            )

        if self.pending_dispatches < 0:
            raise ValueError(
                "pending_dispatches must be >= 0"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "runtime_state": self.runtime_state,
            "active_dispatches": self.active_dispatches,
            "registered_providers": self.registered_providers,
            "pending_dispatches": self.pending_dispatches,
        }


class GovernanceDiagnosticsService:
    """
    Aggregates governance runtime state for debugging.

    Every method is a pure read: it calls the reader callables
    supplied at construction time and returns a plain value or dict,
    never mutating anything they reach into and never causing a side
    effect of its own (no writes, no state transitions, no
    background work started). Two calls to the same method with no
    intervening change in underlying state always return the same
    result.

    Readers are injected rather than this service knowing how to
    read a specific runtime type, so the same service shape works
    for a live delivery runtime (worker, scheduler, and provider
    registry all present) and a stateless per-request context that
    only has a provider registry to read from.
    """

    def __init__(
        self,
        *,
        runtime_state: Callable[[], str],
        active_dispatches: Callable[[], int],
        pending_dispatches: Callable[[], int],
        registered_providers: Callable[[], int],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime_state = runtime_state
        self._active_dispatches = active_dispatches
        self._pending_dispatches = pending_dispatches
        self._registered_providers = registered_providers

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def runtime(self) -> dict[str, object]:
        """
        Return a read-only summary of the runtime's current
        lifecycle state.
        """

        return {
            "state": self._runtime_state(),
            "active_dispatches": self._active_dispatches(),
        }

    def scheduler(self) -> dict[str, object]:
        """
        Return a read-only summary of the scheduler's pending work.
        """

        return {
            "pending_dispatches": self._pending_dispatches(),
        }

    def providers(self) -> dict[str, object]:
        """
        Return a read-only summary of the provider registry.
        """

        return {
            "registered_providers": self._registered_providers(),
        }

    def snapshot(self) -> GovernanceDiagnostics:
        """
        Return a single combined diagnostics snapshot, aggregating
        runtime(), scheduler(), and providers() as of the same
        moment.
        """

        runtime_summary = self.runtime()
        scheduler_summary = self.scheduler()
        providers_summary = self.providers()

        return GovernanceDiagnostics(
            generated_at=self._clock(),
            runtime_state=runtime_summary["state"],
            active_dispatches=runtime_summary["active_dispatches"],
            registered_providers=(
                providers_summary["registered_providers"]
            ),
            pending_dispatches=(
                scheduler_summary["pending_dispatches"]
            ),
        )
