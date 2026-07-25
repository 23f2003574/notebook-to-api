from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from . import runtime

REQUIRED_SERVICES: tuple = (
    "logging_service",
    "tracing_service",
    "metrics_collector",
    "alert_manager",
    "notification_service",
    "slo_manager",
    "recovery_coordinator",
    "chaos_framework",
    "diagnostics_service",
    "capacity_monitor",
    "insights_service",
    "observability_dashboard",
)

SUBSYSTEM_NAME = "observability_reliability"


@dataclass(frozen=True)
class BootstrapValidationResult:
    """One immutable outcome of validating the subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class DeploymentObservabilityBootstrapError(RuntimeError):
    """Raised when the observability subsystem fails startup validation."""

    def __init__(self, result: BootstrapValidationResult) -> None:
        self.result = result
        detail = (
            f" (missing: {', '.join(result.missing_services)})"
            if result.missing_services
            else ""
        )
        super().__init__(
            "observability subsystem bootstrap validation failed" + detail
        )


class DeploymentObservabilityBootstrap:
    """
    Wires together every Observability & Reliability service singleton
    and validates that the subsystem is ready before the governance
    runtime starts serving traffic.
    """

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._lock = Lock()

    def register(self) -> dict:
        from .deployment_alerts import get_deployment_alert_manager
        from .deployment_capacity import get_deployment_capacity_monitor
        from .deployment_chaos import get_deployment_chaos_framework
        from .deployment_diagnostics import get_deployment_diagnostics_service
        from .deployment_insights import get_deployment_insights_service
        from .deployment_logging import get_deployment_logging_service
        from .deployment_metrics import get_deployment_metrics_collector
        from .deployment_notifications import get_deployment_notification_service
        from .deployment_observability_dashboard import (
            get_deployment_observability_dashboard,
        )
        from .deployment_recovery import get_deployment_recovery_coordinator
        from .deployment_slo import get_deployment_slo_manager
        from .deployment_tracing import get_deployment_tracing_service

        services = {
            "logging_service": get_deployment_logging_service(),
            "tracing_service": get_deployment_tracing_service(),
            "metrics_collector": get_deployment_metrics_collector(),
            "alert_manager": get_deployment_alert_manager(),
            "notification_service": get_deployment_notification_service(),
            "slo_manager": get_deployment_slo_manager(),
            "recovery_coordinator": get_deployment_recovery_coordinator(),
            "chaos_framework": get_deployment_chaos_framework(),
            "diagnostics_service": get_deployment_diagnostics_service(),
            "capacity_monitor": get_deployment_capacity_monitor(),
            "insights_service": get_deployment_insights_service(),
            "observability_dashboard": get_deployment_observability_dashboard(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def validate(
        self, *, timestamp: Optional[datetime] = None
    ) -> BootstrapValidationResult:
        with self._lock:
            services = dict(self._services)
        if not services:
            services = self.register()

        missing = tuple(
            name for name in REQUIRED_SERVICES if services.get(name) is None
        )
        result = BootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise DeploymentObservabilityBootstrapError(result)
        return result

    def health_check(self) -> dict:
        with self._lock:
            dashboard = self._services.get("observability_dashboard")
            registered = tuple(sorted(self._services))
        if dashboard is None:
            raise DeploymentObservabilityBootstrapError(
                BootstrapValidationResult(
                    valid=False,
                    registered_services=registered,
                    missing_services=("observability_dashboard",),
                )
            )
        return dashboard.health()


_bootstrap = DeploymentObservabilityBootstrap()


def get_deployment_observability_bootstrap() -> DeploymentObservabilityBootstrap:
    return _bootstrap


def bootstrap_observability_subsystem() -> BootstrapValidationResult:
    """
    Wire, validate, and register the Observability & Reliability
    subsystem with the governance runtime.

    Safe to call more than once: each call re-registers the current
    singletons and re-runs validation.
    """

    bootstrap = get_deployment_observability_bootstrap()
    bootstrap.register()
    result = bootstrap.validate()
    runtime.register_subsystem(SUBSYSTEM_NAME, bootstrap.validate)
    runtime.register_health_check(SUBSYSTEM_NAME, bootstrap.health_check)
    return result
