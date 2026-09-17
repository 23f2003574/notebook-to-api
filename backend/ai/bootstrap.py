from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from .batch_inference import BatchInferenceEngine, get_batch_inference_engine
from .dashboard import ModelDashboardAPI, get_model_dashboard_api
from .export_service import ModelExportService, get_model_export_service
from .inference_analytics import InferenceAnalyticsService, get_inference_analytics_service
from .inference_engine import InferenceEngine, get_inference_engine
from .model_benchmark import ModelBenchmarkService, get_model_benchmark_service
from .model_deployment import ModelDeploymentManager, get_model_deployment_manager
from .model_loader import ModelLoader, ModelValidationError, get_model_loader
from .model_registry import ModelRegistry, UnknownModelError, get_model_registry
from .model_routing import ModelRoutingEngine, get_model_routing_engine
from .model_versioning import ModelVersionManager, get_model_version_manager
from .prompt_templates import PromptTemplateManager, get_prompt_template_manager

REQUIRED_SERVICES: tuple = (
    "model_registry",
    "model_loader",
    "inference_engine",
    "version_manager",
    "template_manager",
    "batch_engine",
    "routing_engine",
    "benchmark_service",
    "deployment_manager",
    "analytics_service",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "ai_model_management_and_inference_platform"


class UnknownServiceError(KeyError):
    pass


class AINotInitializedError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIBootstrapValidationResult:
    """One immutable outcome of validating the AI subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    restored_deployments: tuple = field(default_factory=tuple)
    routing_rules_count: int = 0
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "restored_deployments": list(self.restored_deployments),
            "routing_rules_count": self.routing_rules_count,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class AIBootstrapError(RuntimeError):
    """Raised when the AI subsystem fails startup validation."""

    def __init__(self, result: AIBootstrapValidationResult) -> None:
        self.result = result
        detail = f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        super().__init__("AI subsystem bootstrap validation failed" + detail)


class AIPlatformBootstrap:
    """Wires together every AI Model Management & Inference Platform service singleton."""

    def __init__(self) -> None:
        self._services: dict = {}
        self._restored_deployments: tuple = ()
        self._initialized = False
        self._lock = Lock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def register_services(self) -> dict:
        services = {
            "model_registry": get_model_registry(),
            "model_loader": get_model_loader(),
            "inference_engine": get_inference_engine(),
            "version_manager": get_model_version_manager(),
            "template_manager": get_prompt_template_manager(),
            "batch_engine": get_batch_inference_engine(),
            "routing_engine": get_model_routing_engine(),
            "benchmark_service": get_model_benchmark_service(),
            "deployment_manager": get_model_deployment_manager(),
            "analytics_service": get_inference_analytics_service(),
            "dashboard_api": get_model_dashboard_api(),
            "export_service": get_model_export_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def wire_components(self) -> tuple:
        """Restore models referenced by active deployments into the loader."""
        services = self.registered_services()
        registry: ModelRegistry = services["model_registry"]
        loader: ModelLoader = services["model_loader"]
        deployments: ModelDeploymentManager = services["deployment_manager"]

        restored = []
        for deployment in deployments.list_deployments():
            if not loader.is_loaded(deployment.model_name):
                try:
                    loader.load(deployment.model_name, registry=registry, version=deployment.version)
                except (UnknownModelError, ModelValidationError):
                    continue
            restored.append(deployment.deployment_id)
        with self._lock:
            self._restored_deployments = tuple(restored)
        return self._restored_deployments

    def _count_routing_rules(self) -> int:
        routing_engine: ModelRoutingEngine = self.registered_services()["routing_engine"]
        return len(routing_engine.list_routes())

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    def initialize(self, *, timestamp: Optional[datetime] = None) -> AIBootstrapValidationResult:
        services = self.register_services()
        restored = self.wire_components()
        routing_rules_count = self._count_routing_rules()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = AIBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            restored_deployments=restored,
            routing_rules_count=routing_rules_count,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise AIBootstrapError(result)

        with self._lock:
            self._initialized = True
        return result

    def health_check(self) -> dict:
        if not self._initialized:
            raise AINotInitializedError("AI platform bootstrap is not initialized")
        dashboard: ModelDashboardAPI = self.discover("dashboard_api")
        return {"status": "ok", **dashboard.overview()}

    def shutdown(self) -> None:
        if not self._initialized:
            raise AINotInitializedError("AI platform bootstrap is not initialized")

        with self._lock:
            self._initialized = False
            self._restored_deployments = ()


_bootstrap = AIPlatformBootstrap()


def get_ai_bootstrap() -> AIPlatformBootstrap:
    return _bootstrap


def bootstrap_ai_subsystem() -> AIBootstrapValidationResult:
    """Wire and validate the full AI Model Management & Inference Platform subsystem.

    Safe to call more than once: each call re-registers the current singletons,
    re-restores deployed models, and re-runs validation.
    """
    bootstrap = get_ai_bootstrap()
    return bootstrap.initialize()
