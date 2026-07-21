from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Union

HealthCheckResult = Union[bool, "tuple[bool, str | None]"]

GovernanceHealthCheck = Callable[[], HealthCheckResult]


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
    ) -> None:
        self._checks: dict[str, GovernanceHealthCheck] = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

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
        """

        statuses = self.check_all()

        return GovernanceHealthSummary(
            healthy=all(status.healthy for status in statuses),
            components=statuses,
            checked_at=self._clock(),
        )

    def _run_check(
        self,
        component: str,
        check: GovernanceHealthCheck,
    ) -> GovernanceHealthStatus:
        checked_at = self._clock()

        try:
            result = check()

        except Exception as exc:
            return GovernanceHealthStatus(
                component=component,
                healthy=False,
                message=str(exc),
                checked_at=checked_at,
            )

        if isinstance(result, tuple):
            healthy, message = result

        else:
            healthy, message = bool(result), None

        if not healthy and message is None:
            message = f"{component} reported unhealthy"

        return GovernanceHealthStatus(
            component=component,
            healthy=healthy,
            message=message,
            checked_at=checked_at,
        )
