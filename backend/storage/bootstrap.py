from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from .artifact_integrity import ArtifactIntegrityEngine, get_artifact_integrity_engine
from .artifact_manager import ArtifactManager, get_artifact_manager
from .blob_upload import BlobUploadService, get_blob_upload_service
from .dashboard import StorageDashboardAPI, get_storage_dashboard_api
from .export_service import StorageExportService, get_storage_export_service
from .lifecycle_policy import LifecyclePolicyManager, get_lifecycle_policy_manager
from .object_storage import ObjectStorageEngine, get_object_storage_engine
from .storage_analytics import StorageAnalyticsService, get_storage_analytics_service
from .storage_gc import StorageGarbageCollector, get_storage_garbage_collector
from .storage_registry import StorageMetadata, StorageRegistry, get_storage_registry
from .storage_replication import StorageReplicationEngine, get_storage_replication_engine
from .storage_versioning import StorageVersionManager, get_storage_version_manager

REQUIRED_SERVICES: tuple = (
    "storage_registry",
    "object_storage",
    "artifact_manager",
    "blob_upload",
    "version_manager",
    "integrity_engine",
    "lifecycle_manager",
    "replication_engine",
    "garbage_collector",
    "analytics_service",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "storage_artifact_and_object_management"

_DEFAULT_BACKEND_ID = "local"


class UnknownStorageServiceError(KeyError):
    pass


class StorageNotInitializedError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageBootstrapValidationResult:
    """One immutable outcome of validating the storage subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    restored_artifacts: tuple = field(default_factory=tuple)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "restored_artifacts": list(self.restored_artifacts),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class StorageBootstrapError(RuntimeError):
    """Raised when the storage subsystem fails startup validation."""

    def __init__(self, result: StorageBootstrapValidationResult) -> None:
        self.result = result
        detail = f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        super().__init__("Storage, Artifact & Object Management subsystem bootstrap validation failed" + detail)


class StorageBootstrap:
    """Wires together every Storage, Artifact & Object Management service singleton."""

    def __init__(self) -> None:
        self._services: dict = {}
        self._restored_artifacts: tuple = ()
        self._initialized = False
        self._lock = Lock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def register_services(self) -> dict:
        services = {
            "storage_registry": get_storage_registry(),
            "object_storage": get_object_storage_engine(),
            "artifact_manager": get_artifact_manager(),
            "blob_upload": get_blob_upload_service(),
            "version_manager": get_storage_version_manager(),
            "integrity_engine": get_artifact_integrity_engine(),
            "lifecycle_manager": get_lifecycle_policy_manager(),
            "replication_engine": get_storage_replication_engine(),
            "garbage_collector": get_storage_garbage_collector(),
            "analytics_service": get_storage_analytics_service(),
            "dashboard_api": get_storage_dashboard_api(),
            "export_service": get_storage_export_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def wire_components(self) -> tuple:
        """Ensure the default backend is registered and reconcile artifacts against their objects."""
        services = self.registered_services()
        registry: StorageRegistry = services["storage_registry"]
        object_storage: ObjectStorageEngine = services["object_storage"]
        artifact_manager: ArtifactManager = services["artifact_manager"]

        if registry.get(_DEFAULT_BACKEND_ID) is None:
            registry.register(
                _DEFAULT_BACKEND_ID,
                ["read", "write"],
                StorageMetadata(kind="local", region="local", version="1.0.0"),
                status="active",
            )

        restored = [
            artifact.artifact_id
            for artifact in artifact_manager.list_artifacts()
            if object_storage.exists(artifact.object_key)
        ]
        with self._lock:
            self._restored_artifacts = tuple(restored)
        return self._restored_artifacts

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownStorageServiceError(name)
        return service

    def initialize(self, *, timestamp: Optional[datetime] = None) -> StorageBootstrapValidationResult:
        services = self.register_services()
        restored = self.wire_components()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = StorageBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            restored_artifacts=restored,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise StorageBootstrapError(result)

        with self._lock:
            self._initialized = True
        return result

    def health_check(self) -> dict:
        if not self._initialized:
            raise StorageNotInitializedError("storage bootstrap is not initialized")
        dashboard: StorageDashboardAPI = self.discover("dashboard_api")
        return {"status": "ok", **dashboard.overview()}

    def shutdown(self) -> None:
        if not self._initialized:
            raise StorageNotInitializedError("storage bootstrap is not initialized")

        with self._lock:
            self._initialized = False
            self._restored_artifacts = ()


_bootstrap = StorageBootstrap()


def get_storage_bootstrap() -> StorageBootstrap:
    return _bootstrap


def bootstrap_storage_subsystem() -> StorageBootstrapValidationResult:
    """Wire and validate the full Storage, Artifact & Object Management subsystem.

    Safe to call more than once: each call re-registers the current singletons,
    reconciles artifacts against objects, and re-runs validation.
    """
    bootstrap = get_storage_bootstrap()
    return bootstrap.initialize()
