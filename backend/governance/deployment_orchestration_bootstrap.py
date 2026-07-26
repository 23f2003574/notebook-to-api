from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from . import runtime

REQUIRED_SERVICES: tuple = (
    "pipeline_engine",
    "workflow_engine",
    "stage_orchestrator",
    "dependency_graph",
    "scheduler",
    "execution_plan_builder",
    "template_registry",
    "automation_engine",
    "approval_gate",
    "pipeline_recovery_manager",
    "analytics_service",
    "orchestration_dashboard",
)

SUBSYSTEM_NAME = "orchestration_workflow_automation"


class UnknownServiceError(KeyError):
    pass


@dataclass(frozen=True)
class OrchestrationBootstrapValidationResult:
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


class DeploymentOrchestrationBootstrapError(RuntimeError):
    """Raised when the orchestration subsystem fails startup validation."""

    def __init__(self, result: OrchestrationBootstrapValidationResult) -> None:
        self.result = result
        detail = (
            f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        )
        super().__init__("orchestration subsystem bootstrap validation failed" + detail)


class DeploymentOrchestrationBootstrap:
    """Wires together every Orchestration & Workflow Automation service singleton and validates startup."""

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._lock = Lock()

    def register(self) -> dict:
        from .deployment_approval_gate import get_deployment_approval_gate
        from .deployment_automation import get_deployment_automation_engine
        from .deployment_dependency_graph import get_deployment_dependency_graph
        from .deployment_execution_plan import get_deployment_execution_plan_builder
        from .deployment_orchestration_dashboard import get_deployment_orchestration_dashboard
        from .deployment_pipeline import get_deployment_pipeline_engine
        from .deployment_pipeline_recovery import get_deployment_pipeline_recovery_manager
        from .deployment_scheduler import get_deployment_scheduler
        from .deployment_stage_orchestrator import get_deployment_stage_orchestrator
        from .deployment_templates import get_deployment_template_registry
        from .deployment_workflow import get_deployment_workflow_engine
        from .deployment_workflow_analytics import get_deployment_workflow_analytics_service

        services = {
            "pipeline_engine": get_deployment_pipeline_engine(),
            "workflow_engine": get_deployment_workflow_engine(),
            "stage_orchestrator": get_deployment_stage_orchestrator(),
            "dependency_graph": get_deployment_dependency_graph(),
            "scheduler": get_deployment_scheduler(),
            "execution_plan_builder": get_deployment_execution_plan_builder(),
            "template_registry": get_deployment_template_registry(),
            "automation_engine": get_deployment_automation_engine(),
            "approval_gate": get_deployment_approval_gate(),
            "pipeline_recovery_manager": get_deployment_pipeline_recovery_manager(),
            "analytics_service": get_deployment_workflow_analytics_service(),
            "orchestration_dashboard": get_deployment_orchestration_dashboard(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    def validate(
        self, *, timestamp: Optional[datetime] = None
    ) -> OrchestrationBootstrapValidationResult:
        with self._lock:
            services = dict(self._services)
        if not services:
            services = self.register()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = OrchestrationBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise DeploymentOrchestrationBootstrapError(result)
        return result

    def health_check(self) -> dict:
        with self._lock:
            dashboard = self._services.get("orchestration_dashboard")
            registered = tuple(sorted(self._services))
        if dashboard is None:
            raise DeploymentOrchestrationBootstrapError(
                OrchestrationBootstrapValidationResult(
                    valid=False,
                    registered_services=registered,
                    missing_services=("orchestration_dashboard",),
                )
            )
        return {"status": "ok", **dashboard.overview()}


_bootstrap = DeploymentOrchestrationBootstrap()


def get_deployment_orchestration_bootstrap() -> DeploymentOrchestrationBootstrap:
    return _bootstrap


def bootstrap_orchestration_subsystem() -> OrchestrationBootstrapValidationResult:
    """Wire, validate, and register the Orchestration & Workflow Automation subsystem with the governance runtime.

    Safe to call more than once: each call re-registers the current singletons and re-runs validation.
    """

    bootstrap = get_deployment_orchestration_bootstrap()
    bootstrap.register()
    result = bootstrap.validate()
    runtime.register_subsystem(SUBSYSTEM_NAME, bootstrap.validate)
    runtime.register_health_check(SUBSYSTEM_NAME, bootstrap.health_check)
    return result
