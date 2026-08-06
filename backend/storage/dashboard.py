from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from .artifact_manager import ArtifactManager, get_artifact_manager
from .lifecycle_policy import LifecyclePolicyManager, get_lifecycle_policy_manager
from .storage_analytics import StorageAnalyticsService, get_storage_analytics_service
from .storage_registry import StorageRegistry, get_storage_registry
from .storage_replication import StorageReplicationEngine, get_storage_replication_engine


class StorageDashboardAPI:
    """Aggregates the storage subsystem into read-only operational views."""

    def __init__(
        self,
        *,
        artifact_manager: ArtifactManager,
        analytics_service: StorageAnalyticsService,
        storage_registry: Optional[StorageRegistry] = None,
        replication_engine: Optional[StorageReplicationEngine] = None,
        lifecycle_manager: Optional[LifecyclePolicyManager] = None,
    ) -> None:
        self._artifact_manager = artifact_manager
        self._analytics = analytics_service
        self._storage_registry = storage_registry
        self._replication = replication_engine
        self._lifecycle = lifecycle_manager

    def overview(self) -> dict:
        statuses = self._replication.list_status() if self._replication is not None else []
        in_sync = sum(1 for status in statuses if status.in_sync)
        policies = self._lifecycle.list_policies() if self._lifecycle is not None else []
        history = self._lifecycle.list_history() if self._lifecycle is not None else []

        return {
            "storage": self._analytics.summary().to_dict(),
            "replication": {
                "tracked_pairs": len(statuses),
                "in_sync": in_sync,
                "out_of_sync": len(statuses) - in_sync,
            },
            "lifecycle": {
                "policy_count": len(policies),
                "execution_count": len(history),
            },
        }

    def artifacts(self) -> dict:
        artifacts = self._artifact_manager.list_artifacts()
        by_type: dict = {}
        by_namespace: dict = {}
        for artifact in artifacts:
            by_type[artifact.artifact_type.value] = by_type.get(artifact.artifact_type.value, 0) + 1
            by_namespace[artifact.namespace] = by_namespace.get(artifact.namespace, 0) + 1

        return {
            "total": len(artifacts),
            "by_type": by_type,
            "by_namespace": by_namespace,
            "artifacts": [artifact.to_dict() for artifact in artifacts],
        }

    def capacity(self) -> dict:
        summary = self._analytics.summary()
        try:
            forecast = self._analytics.forecast()
        except ValueError:
            forecast = None
        backends = self._storage_registry.list_backends() if self._storage_registry is not None else []

        return {
            "storage_usage_bytes": summary.storage_usage_bytes,
            "object_count": summary.object_count,
            "forecast": forecast,
            "backends": [backend.to_dict() for backend in backends],
        }

    def analytics(self) -> dict:
        return {
            "summary": self._analytics.summary().to_dict(),
            "trends": [trend.to_dict() for trend in self._analytics.trends()],
            "snapshot_count": len(self._analytics.history()),
        }


_storage_dashboard_api = StorageDashboardAPI(
    artifact_manager=get_artifact_manager(),
    analytics_service=get_storage_analytics_service(),
    storage_registry=get_storage_registry(),
    replication_engine=get_storage_replication_engine(),
    lifecycle_manager=get_lifecycle_policy_manager(),
)


def get_storage_dashboard_api() -> StorageDashboardAPI:
    return _storage_dashboard_api


router = APIRouter(prefix="/storage/dashboard", tags=["storage-dashboard"])


@router.get("")
def overview_endpoint(
    dashboard: StorageDashboardAPI = Depends(get_storage_dashboard_api),
) -> dict:
    return dashboard.overview()


@router.get("/artifacts")
def artifacts_endpoint(
    dashboard: StorageDashboardAPI = Depends(get_storage_dashboard_api),
) -> dict:
    return dashboard.artifacts()


@router.get("/capacity")
def capacity_endpoint(
    dashboard: StorageDashboardAPI = Depends(get_storage_dashboard_api),
) -> dict:
    return dashboard.capacity()


@router.get("/analytics")
def analytics_endpoint(
    dashboard: StorageDashboardAPI = Depends(get_storage_dashboard_api),
) -> dict:
    return dashboard.analytics()
