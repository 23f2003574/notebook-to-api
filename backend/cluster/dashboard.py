from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from .cluster_analytics import ClusterAnalyticsService, get_cluster_analytics_service
from .distributed_scheduler import DistributedScheduler, get_distributed_scheduler
from .execution_coordinator import (
    ASSIGNED,
    CANCELLED,
    COMPLETED,
    ExecutionCoordinator,
    FAILED,
    QUEUED,
    RUNNING,
    get_execution_coordinator,
)
from .job_dispatcher import DistributedJobDispatcher, get_job_dispatcher
from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service
from .worker_registry import WorkerRegistry, get_worker_registry

_EXECUTION_STATES = (QUEUED, ASSIGNED, RUNNING, COMPLETED, FAILED, CANCELLED)


class ClusterDashboardAPI:
    """Aggregates worker, execution, scheduling, and analytics state into read-only dashboard views."""

    def __init__(
        self,
        registry: WorkerRegistry,
        discovery: WorkerDiscoveryService,
        dispatcher: DistributedJobDispatcher,
        scheduler: DistributedScheduler,
        coordinator: ExecutionCoordinator,
        analytics: ClusterAnalyticsService,
    ) -> None:
        self._registry = registry
        self._discovery = discovery
        self._dispatcher = dispatcher
        self._scheduler = scheduler
        self._coordinator = coordinator
        self._analytics = analytics

    def overview(self) -> dict:
        workers = self._registry.list_workers()
        healthy_count = sum(1 for worker in workers if self._discovery.get_health(worker.worker_id) != "unhealthy")
        online_count = sum(1 for worker in workers if worker.status == "online")

        execution_counts = {state: 0 for state in _EXECUTION_STATES}
        for session in self._coordinator.list_executions():
            execution_counts[session.state] = execution_counts.get(session.state, 0) + 1

        return {
            "workers": {
                "total": len(workers),
                "online": online_count,
                "healthy": healthy_count,
                "unhealthy": len(workers) - healthy_count,
            },
            "executions": execution_counts,
            "scheduling": {
                **self._scheduler.stats(),
                "queue_depth": len(self._dispatcher.queue_status()),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def workers(self) -> list:
        rows = [
            {
                "worker_id": node.worker_id,
                "status": node.status,
                "health": self._discovery.get_health(node.worker_id),
                "capabilities": list(node.capabilities),
                "active_jobs": self._discovery.get_load(node.worker_id),
                "hostname": node.metadata.hostname,
                "region": node.metadata.region,
            }
            for node in self._registry.list_workers()
        ]
        return sorted(rows, key=lambda row: row["worker_id"])

    def executions(self, *, state: Optional[str] = None) -> list:
        return [session.to_dict() for session in self._coordinator.list_executions(state=state)]

    def analytics(self, *, capability: Optional[str] = None) -> dict:
        if capability is not None:
            return self._analytics.summary(capability)
        return {
            "overall": self._analytics.summary(),
            "by_capability": {cap: self._analytics.summary(cap) for cap in self._analytics.capabilities()},
        }

    def snapshot(self) -> dict:
        """A single combined view of every dashboard section, e.g. for a full export or one-shot page load."""
        return {
            "overview": self.overview(),
            "workers": self.workers(),
            "executions": self.executions(),
            "analytics": self.analytics(),
        }


_cluster_dashboard_api = ClusterDashboardAPI(
    get_worker_registry(),
    get_worker_discovery_service(),
    get_job_dispatcher(),
    get_distributed_scheduler(),
    get_execution_coordinator(),
    get_cluster_analytics_service(),
)


def get_cluster_dashboard_api() -> ClusterDashboardAPI:
    return _cluster_dashboard_api


router = APIRouter(prefix="/cluster/dashboard", tags=["cluster-dashboard"])


@router.get("")
def overview_endpoint(
    dashboard: ClusterDashboardAPI = Depends(get_cluster_dashboard_api),
) -> dict:
    return dashboard.overview()


@router.get("/workers")
def workers_endpoint(
    dashboard: ClusterDashboardAPI = Depends(get_cluster_dashboard_api),
) -> list:
    return dashboard.workers()


@router.get("/executions")
def executions_endpoint(
    state: Optional[str] = None,
    dashboard: ClusterDashboardAPI = Depends(get_cluster_dashboard_api),
) -> list:
    return dashboard.executions(state=state)


@router.get("/analytics")
def analytics_endpoint(
    capability: Optional[str] = None,
    dashboard: ClusterDashboardAPI = Depends(get_cluster_dashboard_api),
) -> dict:
    return dashboard.analytics(capability=capability)
