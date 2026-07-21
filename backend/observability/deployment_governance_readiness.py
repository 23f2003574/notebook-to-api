from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .deployment_governance_health import (
    HealthCheckResult,
    evaluate_component_check,
)

GovernanceReadinessCheck = Callable[[], HealthCheckResult]


def count_registered_providers(provider_registry: object) -> "int | None":
    """
    Return how many providers are registered on provider_registry,
    tolerating either the real GovernanceIntegrityProviderRegistry's
    list() or the list_providers() duck-typed protocol some delivery
    runtime callers use in its place.

    Returns None (rather than 0) if provider_registry is None or
    exposes neither method, since that means "unknown" and not "zero
    providers registered" — callers that need a plain count (e.g.
    diagnostics) should treat None as 0; callers making a readiness
    judgment should treat it as unverifiable and not automatically
    unready.
    """

    if provider_registry is None:
        return None

    if hasattr(provider_registry, "list"):
        registrations = provider_registry.list()

    elif hasattr(provider_registry, "list_providers"):
        registrations = provider_registry.list_providers()

    else:
        return None

    return len(registrations) if registrations is not None else 0


def diagnostics_readiness_check(diagnostics_service) -> HealthCheckResult:
    """
    Adapt a GovernanceDiagnosticsService into a readiness check
    result: diagnostics can only be generated once the runtime state
    it reads from (scheduler, provider registry) is wired in, so a
    successful snapshot implies those components are ready to be
    introspected.
    """

    try:
        diagnostics_service.snapshot()

    except Exception as exc:
        return False, str(exc)

    return True


@dataclass(frozen=True)
class GovernanceReadinessStatus:
    """
    The readiness of one governance runtime component, as of one
    point-in-time check.
    """

    component: str

    ready: bool

    reason: str | None

    checked_at: datetime

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError(
                "checked_at must be timezone-aware"
            )

        if self.ready:
            if self.reason is not None:
                raise ValueError(
                    "reason must not be set when ready is True"
                )

        else:
            if self.reason is None:
                raise ValueError(
                    "reason must be set when ready is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "ready": self.ready,
            "reason": self.reason,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class GovernanceReadinessSummary:
    """
    A point-in-time rollup of every registered component's
    readiness.
    """

    ready: bool

    components: "tuple[GovernanceReadinessStatus, ...]"

    checked_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "checked_at": self.checked_at.isoformat(),
            "components": [
                status.to_dict() for status in self.components
            ],
        }


class GovernanceReadinessService:
    """
    Central registry of readiness checks for governance runtime
    components (delivery worker, scheduler, provider registry,
    runtime running state, and any other component that determines
    whether the governance subsystem can accept work).

    Readiness is deliberately independent of health: a component can
    be healthy (not erroring) while still not ready (e.g. a worker
    that has not finished initializing), and this service never
    consults GovernanceHealthService to derive its results.

    Each component registers a zero-argument check callable that
    returns either a bool, or a (bool, reason) tuple. A check that
    raises is treated as not-ready rather than propagating, so one
    failing component never prevents the others from being checked.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._checks: dict[str, GovernanceReadinessCheck] = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def register(
        self,
        component: str,
        check: GovernanceReadinessCheck,
    ) -> None:
        """
        Register a readiness check for component.

        Raises ValueError if component is already registered.
        """

        if component in self._checks:
            raise ValueError(
                f"component '{component}' is already registered"
            )

        self._checks[component] = check

    def check(self, component: str) -> GovernanceReadinessStatus:
        """
        Run the readiness check registered for component.

        Raises LookupError if no check is registered for component.
        """

        check = self._checks.get(component)

        if check is None:
            raise LookupError(
                f"no readiness check registered for component "
                f"'{component}'"
            )

        return self._run_check(component, check)

    def check_all(self) -> "tuple[GovernanceReadinessStatus, ...]":
        """
        Run every registered readiness check, ordered by component
        name for deterministic output. A failing check does not stop
        the remaining checks from running.
        """

        return tuple(
            self._run_check(name, self._checks[name])
            for name in sorted(self._checks)
        )

    def summary(self) -> GovernanceReadinessSummary:
        """
        Run every registered readiness check and return the
        aggregated result: overall ready is True only if every
        component is ready.
        """

        statuses = self.check_all()

        return GovernanceReadinessSummary(
            ready=all(status.ready for status in statuses),
            components=statuses,
            checked_at=self._clock(),
        )

    def _run_check(
        self,
        component: str,
        check: GovernanceReadinessCheck,
    ) -> GovernanceReadinessStatus:
        checked_at = self._clock()

        ready, reason = evaluate_component_check(
            component,
            check,
            default_message=f"{component} is not ready",
        )

        return GovernanceReadinessStatus(
            component=component,
            ready=ready,
            reason=reason,
            checked_at=checked_at,
        )
