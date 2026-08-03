import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.cluster_analytics import ClusterAnalyticsService
from backend.cluster.dashboard import (
    ClusterDashboardAPI,
    get_cluster_dashboard_api,
    router as dashboard_router,
)
from backend.cluster.distributed_scheduler import DistributedScheduler
from backend.cluster.execution_coordinator import ASSIGNED, ExecutionCoordinator, QUEUED
from backend.cluster.job_dispatcher import DistributedJobDispatcher
from backend.cluster.worker_discovery import WorkerDiscoveryService
from backend.cluster.worker_registry import WorkerMetadata, WorkerRegistry


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0")


@pytest.fixture
def registry() -> WorkerRegistry:
    return WorkerRegistry()


@pytest.fixture
def discovery(registry: WorkerRegistry) -> WorkerDiscoveryService:
    return WorkerDiscoveryService(registry, stale_after_seconds=300.0)


@pytest.fixture
def dispatcher(discovery: WorkerDiscoveryService) -> DistributedJobDispatcher:
    return DistributedJobDispatcher(discovery)


@pytest.fixture
def scheduler(discovery: WorkerDiscoveryService) -> DistributedScheduler:
    return DistributedScheduler(discovery)


@pytest.fixture
def analytics() -> ClusterAnalyticsService:
    return ClusterAnalyticsService()


@pytest.fixture
def coordinator(dispatcher: DistributedJobDispatcher, scheduler: DistributedScheduler, analytics: ClusterAnalyticsService) -> ExecutionCoordinator:
    return ExecutionCoordinator(dispatcher, scheduler=scheduler, analytics=analytics)


@pytest.fixture
def dashboard(
    registry: WorkerRegistry,
    discovery: WorkerDiscoveryService,
    dispatcher: DistributedJobDispatcher,
    scheduler: DistributedScheduler,
    coordinator: ExecutionCoordinator,
    analytics: ClusterAnalyticsService,
) -> ClusterDashboardAPI:
    return ClusterDashboardAPI(registry, discovery, dispatcher, scheduler, coordinator, analytics)


@pytest.fixture
def client(dashboard: ClusterDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_cluster_dashboard_api] = lambda: dashboard
    return TestClient(app)


def test_overview_counts_workers_by_status_and_health(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, dashboard: ClusterDashboardAPI
):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["parse"], make_metadata())
    discovery.set_health("worker-2", "unhealthy")

    overview = dashboard.overview()

    assert overview["workers"]["total"] == 2
    assert overview["workers"]["online"] == 2
    assert overview["workers"]["healthy"] == 1
    assert overview["workers"]["unhealthy"] == 1


def test_overview_counts_executions_by_state(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, dashboard: ClusterDashboardAPI
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.submit("job-2", "export")

    overview = dashboard.overview()

    assert overview["executions"][ASSIGNED] == 1
    assert overview["executions"][QUEUED] == 1


def test_overview_includes_scheduling_stats_and_queue_depth(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, dashboard: ClusterDashboardAPI
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.submit("job-2", "export")

    overview = dashboard.overview()

    assert overview["scheduling"]["queue_depth"] == 1
    assert "scheduled" in overview["scheduling"]
    assert "generated_at" in overview


def test_workers_lists_registry_and_discovery_state(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, dashboard: ClusterDashboardAPI
):
    registry.register("worker-1", ["parse"], make_metadata(hostname="node-1"))
    discovery.set_load("worker-1", 3)

    rows = dashboard.workers()

    assert len(rows) == 1
    assert rows[0]["worker_id"] == "worker-1"
    assert rows[0]["active_jobs"] == 3
    assert rows[0]["hostname"] == "node-1"


def test_workers_sorted_by_worker_id(registry: WorkerRegistry, dashboard: ClusterDashboardAPI):
    registry.register("worker-b", ["parse"], make_metadata())
    registry.register("worker-a", ["parse"], make_metadata())

    rows = dashboard.workers()

    assert [row["worker_id"] for row in rows] == ["worker-a", "worker-b"]


def test_executions_returns_session_summaries(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, dashboard: ClusterDashboardAPI
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    rows = dashboard.executions()

    assert len(rows) == 1
    assert rows[0]["job_id"] == "job-1"


def test_executions_filters_by_state(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, dashboard: ClusterDashboardAPI
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.submit("job-2", "export")

    queued = dashboard.executions(state=QUEUED)

    assert [row["job_id"] for row in queued] == ["job-2"]


def test_analytics_without_capability_returns_overall_and_breakdown(
    analytics: ClusterAnalyticsService, dashboard: ClusterDashboardAPI
):
    analytics.record("parse", worker_count=2, active_jobs=1, queue_depth=0)
    analytics.record("export", worker_count=1, active_jobs=1, queue_depth=0)

    result = dashboard.analytics()

    assert result["overall"]["sample_count"] == 2
    assert set(result["by_capability"].keys()) == {"parse", "export"}


def test_analytics_with_capability_returns_scoped_summary(
    analytics: ClusterAnalyticsService, dashboard: ClusterDashboardAPI
):
    analytics.record("parse", worker_count=2, active_jobs=1, queue_depth=0)
    analytics.record("export", worker_count=1, active_jobs=1, queue_depth=0)

    result = dashboard.analytics(capability="parse")

    assert result["sample_count"] == 1


def test_api_overview(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/dashboard")

    assert response.status_code == 200
    assert response.json()["workers"]["total"] == 1


def test_api_workers(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/dashboard/workers")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_executions(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    response = client.get("/cluster/dashboard/executions")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_analytics(client: TestClient, analytics: ClusterAnalyticsService):
    analytics.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    response = client.get("/cluster/dashboard/analytics")

    assert response.status_code == 200
    assert response.json()["overall"]["sample_count"] == 1
