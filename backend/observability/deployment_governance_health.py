from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .deployment_governance_liveness import GovernanceLivenessService
    from .deployment_governance_diagnostics import (
        GovernanceDiagnosticsService,
    )
    from .deployment_governance_dependency_graph import (
        DependencyValidationResult,
    )
    from .deployment_governance_lifecycle import (
        GovernanceLifecycleManager,
    )
    from .deployment_governance_event_bus import GovernanceEventBus

HealthCheckResult = Union[bool, "tuple[bool, str | None]"]

GovernanceHealthCheck = Callable[[], HealthCheckResult]


def liveness_health_check(
    liveness_service: "GovernanceLivenessService",
) -> HealthCheckResult:
    """
    Adapt a GovernanceLivenessService into a health check result, so
    process liveness can be registered as one more component on a
    GovernanceHealthService without every caller re-deriving the same
    alive -> (ok, message) mapping.
    """

    status = liveness_service.check()

    if status.alive:
        return True

    return False, "liveness service has not been started"


def diagnostics_health_check(
    diagnostics_service: "GovernanceDiagnosticsService",
) -> HealthCheckResult:
    """
    Adapt a GovernanceDiagnosticsService into a health check result:
    diagnostics generation is read-only and should never fail while
    the runtime it reads from is otherwise sound, so any exception
    raised while building a snapshot is treated as unhealthy.
    """

    try:
        diagnostics_service.snapshot()

    except Exception as exc:
        return False, str(exc)

    return True


def dependency_graph_health_check(
    result: "DependencyValidationResult",
) -> HealthCheckResult:
    """
    Adapt a DependencyValidationResult into a health check result, so
    a governance dependency graph's validity can be registered as one
    more component on a GovernanceHealthService.
    """

    if result.valid:
        return True

    reasons = []

    if result.missing:
        reasons.append("missing: " + ", ".join(result.missing))

    if result.cycles:
        reasons.append(
            "cycles: "
            + "; ".join(" -> ".join(cycle) for cycle in result.cycles)
        )

    return False, "; ".join(reasons)


def lifecycle_health_check(
    manager: "GovernanceLifecycleManager",
) -> HealthCheckResult:
    """
    Adapt a GovernanceLifecycleManager into a health check result:
    healthy only if every one of its registered components currently
    reports started.
    """

    not_started = [
        component.name
        for component in manager.status()
        if not component.started
    ]

    if not_started:
        return False, "not started: " + ", ".join(sorted(not_started))

    return True


def evaluate_component_check(
    component: str,
    check: Callable[[], HealthCheckResult],
    *,
    default_message: str,
) -> "tuple[bool, str | None]":
    """
    Run a zero-argument component check callable and normalize its
    result to an (ok, message) pair.

    The check may return a bool, or a (bool, message) tuple. A check
    that raises is treated as not-ok (with the exception text as the
    message) rather than propagating, so one failing component never
    prevents the others from being checked. Shared between
    GovernanceHealthService and GovernanceReadinessService, whose
    check-running semantics are otherwise identical.
    """

    try:
        result = check()

    except Exception as exc:
        return False, str(exc)

    if isinstance(result, tuple):
        ok, message = result

    else:
        ok, message = bool(result), None

    if not ok and message is None:
        message = default_message

    return ok, message


@dataclass(frozen=True)
class GovernanceHealthStatus:
    """
    The health of one governance runtime component, as of one
    point-in-time check.
    """

    component: str

    healthy: bool

    message: str | None

    checked_at: datetime

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError(
                "checked_at must be timezone-aware"
            )

        if self.healthy:
            if self.message is not None:
                raise ValueError(
                    "message must not be set when healthy is True"
                )

        else:
            if self.message is None:
                raise ValueError(
                    "message must be set when healthy is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "healthy": self.healthy,
            "message": self.message,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class GovernanceHealthSummary:
    """
    A point-in-time rollup of every registered component's health.
    """

    healthy: bool

    components: "tuple[GovernanceHealthStatus, ...]"

    checked_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "checked_at": self.checked_at.isoformat(),
            "components": [
                status.to_dict() for status in self.components
            ],
        }


class GovernanceHealthService:
    """
    Central registry of health checks for governance runtime
    components (delivery runtime, metrics bootstrap, logging
    bootstrap, provider registry, and any other component that can
    report its own health).

    Each component registers a zero-argument check callable that
    returns either a bool, or a (bool, message) tuple. A check that
    raises is treated as unhealthy rather than propagating, so one
    failing component never prevents the others from being checked.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        self._checks: dict[str, GovernanceHealthCheck] = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register(
        self,
        component: str,
        check: GovernanceHealthCheck,
    ) -> None:
        """
        Register a health check for component.

        Raises ValueError if component is already registered.
        """

        if component in self._checks:
            raise ValueError(
                f"component '{component}' is already registered"
            )

        self._checks[component] = check

    def check(self, component: str) -> GovernanceHealthStatus:
        """
        Run the health check registered for component.

        Raises LookupError if no check is registered for component.
        """

        check = self._checks.get(component)

        if check is None:
            raise LookupError(
                f"no health check registered for component "
                f"'{component}'"
            )

        return self._run_check(component, check)

    def check_all(self) -> "tuple[GovernanceHealthStatus, ...]":
        """
        Run every registered health check, ordered by component name
        for deterministic output. A failing check does not stop the
        remaining checks from running.
        """

        return tuple(
            self._run_check(name, self._checks[name])
            for name in sorted(self._checks)
        )

    def summary(self) -> GovernanceHealthSummary:
        """
        Run every registered health check and return the aggregated
        result: overall healthy is True only if every component is
        healthy.

        Publishes a "health_check_completed" event if this service
        was constructed with an event_bus.
        """

        statuses = self.check_all()

        summary = GovernanceHealthSummary(
            healthy=all(status.healthy for status in statuses),
            components=statuses,
            checked_at=self._clock(),
        )

        if self._event_bus is not None:
            self._event_bus.publish(
                "health_check_completed",
                source="health_service",
                payload={
                    "healthy": summary.healthy,
                    "component_count": len(summary.components),
                },
            )

        return summary

    def _run_check(
        self,
        component: str,
        check: GovernanceHealthCheck,
    ) -> GovernanceHealthStatus:
        checked_at = self._clock()

        healthy, message = evaluate_component_check(
            component,
            check,
            default_message=f"{component} reported unhealthy",
        )

        return GovernanceHealthStatus(
            component=component,
            healthy=healthy,
            message=message,
            checked_at=checked_at,
        )
