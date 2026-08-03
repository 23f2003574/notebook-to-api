from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from .auto_scaling import AutoScalingEngine, get_auto_scaling_engine
from .cluster_analytics import ClusterAnalyticsService, get_cluster_analytics_service
from .dashboard import ClusterDashboardAPI, get_cluster_dashboard_api
from .distributed_scheduler import DistributedScheduler, get_distributed_scheduler
from .execution_coordinator import ExecutionCoordinator, get_execution_coordinator
from .export_service import ClusterExportService, get_cluster_export_service
from .fault_tolerance import FaultToleranceManager, get_fault_tolerance_manager
from .job_dispatcher import DistributedJobDispatcher, get_job_dispatcher
from .task_serializer import TaskSerializationEngine, get_task_serialization_engine
from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service
from .worker_health import WorkerHealthManager, get_worker_health_manager
from .worker_registry import WorkerRegistry, get_worker_registry

REQUIRED_SERVICES: tuple = (
    "worker_registry",
    "worker_discovery",
    "job_dispatcher",
    "task_serializer",
    "execution_coordinator",
    "worker_health",
    "scheduler",
    "auto_scaler",
    "fault_tolerance",
    "analytics_service",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "distributed_execution_and_compute_orchestration"


class UnknownServiceError(KeyError):
    pass


class ClusterNotInitializedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClusterBootstrapValidationResult:
    """One immutable outcome of validating the cluster subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    restored_workers: tuple = field(default_factory=tuple)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "restored_workers": list(self.restored_workers),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class ClusterBootstrapError(RuntimeError):
    """Raised when the cluster subsystem fails startup validation."""

    def __init__(self, result: ClusterBootstrapValidationResult) -> None:
        self.result = result
        detail = f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        super().__init__("Distributed execution subsystem bootstrap validation failed" + detail)


class DistributedExecutionBootstrap:
    """Wires together every Distributed Execution & Compute Orchestration service singleton."""

    def __init__(self) -> None:
        self._services: dict = {}
        self._restored_workers: tuple = ()
        self._initialized = False
        self._lock = Lock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def register_services(self) -> dict:
        services = {
            "worker_registry": get_worker_registry(),
            "worker_discovery": get_worker_discovery_service(),
            "job_dispatcher": get_job_dispatcher(),
            "task_serializer": get_task_serialization_engine(),
            "execution_coordinator": get_execution_coordinator(),
            "worker_health": get_worker_health_manager(),
            "scheduler": get_distributed_scheduler(),
            "auto_scaler": get_auto_scaling_engine(),
            "fault_tolerance": get_fault_tolerance_manager(),
            "analytics_service": get_cluster_analytics_service(),
            "dashboard_api": get_cluster_dashboard_api(),
            "export_service": get_cluster_export_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def wire_components(self) -> tuple:
        """Run an initial health probe against every currently registered worker."""
        services = self.registered_services()
        registry: WorkerRegistry = services["worker_registry"]
        health: WorkerHealthManager = services["worker_health"]

        restored = []
        for worker in registry.list_workers():
            try:
                health.check(worker.worker_id)
            except KeyError:
                continue
            restored.append(worker.worker_id)
        with self._lock:
            self._restored_workers = tuple(restored)
        return self._restored_workers

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    def initialize(self, *, timestamp: Optional[datetime] = None) -> ClusterBootstrapValidationResult:
        services = self.register_services()
        restored = self.wire_components()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = ClusterBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            restored_workers=restored,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise ClusterBootstrapError(result)

        with self._lock:
            self._initialized = True
        return result

    def health_check(self) -> dict:
        if not self._initialized:
            raise ClusterNotInitializedError("distributed execution bootstrap is not initialized")
        dashboard: ClusterDashboardAPI = self.discover("dashboard_api")
        return {"status": "ok", **dashboard.overview()}

    def shutdown(self) -> None:
        if not self._initialized:
            raise ClusterNotInitializedError("distributed execution bootstrap is not initialized")

        with self._lock:
            self._initialized = False
            self._restored_workers = ()


_bootstrap = DistributedExecutionBootstrap()


def get_cluster_bootstrap() -> DistributedExecutionBootstrap:
    return _bootstrap


def bootstrap_cluster_subsystem() -> ClusterBootstrapValidationResult:
    """Wire and validate the full Distributed Execution & Compute Orchestration subsystem.

    Safe to call more than once: each call re-registers the current singletons,
    re-probes worker health, and re-runs validation.
    """
    bootstrap = get_cluster_bootstrap()
    return bootstrap.initialize()
