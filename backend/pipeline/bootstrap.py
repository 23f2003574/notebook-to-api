from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from .checkpoint_manager import get_checkpoint_recovery_manager
from .data_sources import get_data_source_manager
from .data_validation import get_data_validation_engine
from .dashboard import get_pipeline_dashboard_api
from .etl_engine import ETLWorkflowEngine, get_etl_workflow_engine
from .export_service import get_pipeline_export_service
from .pipeline_analytics import get_pipeline_analytics_service
from .pipeline_executor import ExecutionState, InvalidStateTransitionError, get_pipeline_execution_engine
from .pipeline_registry import get_pipeline_registry
from .pipeline_scheduler import PipelineScheduler, get_pipeline_scheduler
from .schema_registry import get_schema_registry
from .transformation_engine import get_data_transformation_engine

REQUIRED_SERVICES: tuple = (
    "pipeline_registry",
    "data_source_manager",
    "transformation_engine",
    "etl_engine",
    "validation_engine",
    "schema_registry",
    "scheduler",
    "execution_engine",
    "checkpoint_manager",
    "analytics_service",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "data_pipeline_and_etl_framework"


class UnknownServiceError(KeyError):
    pass


class PipelineNotInitializedError(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelineBootstrapValidationResult:
    """One immutable outcome of validating the pipeline subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    restored_schedules: tuple = field(default_factory=tuple)
    loaded_schemas: int = 0
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "restored_schedules": list(self.restored_schedules),
            "loaded_schemas": self.loaded_schemas,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class PipelineBootstrapError(RuntimeError):
    """Raised when the pipeline subsystem fails startup validation."""

    def __init__(self, result: PipelineBootstrapValidationResult) -> None:
        self.result = result
        detail = f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        super().__init__("pipeline subsystem bootstrap validation failed" + detail)


class PipelineBootstrap:
    """Wires together every Data Pipeline & ETL Framework service singleton."""

    def __init__(self) -> None:
        self._services: dict = {}
        self._restored_schedules: tuple = ()
        self._initialized = False
        self._lock = Lock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def register_services(self) -> dict:
        services = {
            "pipeline_registry": get_pipeline_registry(),
            "data_source_manager": get_data_source_manager(),
            "transformation_engine": get_data_transformation_engine(),
            "etl_engine": get_etl_workflow_engine(),
            "validation_engine": get_data_validation_engine(),
            "schema_registry": get_schema_registry(),
            "scheduler": get_pipeline_scheduler(),
            "execution_engine": get_pipeline_execution_engine(),
            "checkpoint_manager": get_checkpoint_recovery_manager(),
            "analytics_service": get_pipeline_analytics_service(),
            "dashboard_api": get_pipeline_dashboard_api(),
            "export_service": get_pipeline_export_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def wire_components(self) -> tuple:
        """Restore scheduled pipelines: keep active schedules whose workflow still
        exists, and cancel any that reference a workflow that's since disappeared."""

        services = self.registered_services()
        scheduler: PipelineScheduler = services["scheduler"]
        workflows: ETLWorkflowEngine = services["etl_engine"]

        restored = []
        for schedule in scheduler.list_schedules():
            if schedule.status != "active":
                continue
            if workflows.workflow_exists(schedule.workflow_name):
                restored.append(schedule.schedule_id)
            else:
                scheduler.cancel(schedule.schedule_id)
        with self._lock:
            self._restored_schedules = tuple(restored)
        return self._restored_schedules

    def _load_schemas(self) -> int:
        schema_registry = self.registered_services()["schema_registry"]
        return len(schema_registry.list_schemas())

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    def initialize(self, *, timestamp: Optional[datetime] = None) -> PipelineBootstrapValidationResult:
        services = self.register_services()
        restored = self.wire_components()
        loaded_schemas = self._load_schemas()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = PipelineBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            restored_schedules=restored,
            loaded_schemas=loaded_schemas,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise PipelineBootstrapError(result)

        with self._lock:
            self._initialized = True
        return result

    def health_check(self) -> dict:
        if not self._initialized:
            raise PipelineNotInitializedError("pipeline bootstrap is not initialized")
        dashboard = self.discover("dashboard_api")
        return {"status": "ok", **dashboard.overview()}

    def shutdown(self) -> None:
        if not self._initialized:
            raise PipelineNotInitializedError("pipeline bootstrap is not initialized")

        execution_engine = self._services.get("execution_engine")
        if execution_engine is not None:
            for run in execution_engine.list_runs(state=ExecutionState.QUEUED):
                try:
                    execution_engine.cancel(run.run_id)
                except InvalidStateTransitionError:
                    continue

        with self._lock:
            self._initialized = False
            self._restored_schedules = ()


_bootstrap = PipelineBootstrap()


def get_pipeline_bootstrap() -> PipelineBootstrap:
    return _bootstrap


def bootstrap_pipeline_subsystem() -> PipelineBootstrapValidationResult:
    """Wire and validate the full Data Pipeline & ETL Framework subsystem.

    Safe to call more than once: each call re-registers the current singletons,
    re-restores scheduled pipelines, and re-runs validation.
    """
    bootstrap = get_pipeline_bootstrap()
    return bootstrap.initialize()
