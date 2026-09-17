import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.cluster_analytics import ClusterAnalyticsService
from backend.cluster.dashboard import ClusterDashboardAPI
from backend.cluster.distributed_scheduler import DistributedScheduler
from backend.cluster.execution_coordinator import ExecutionCoordinator
from backend.cluster.export_service import (
    ClusterExport,
    ClusterExportService,
    get_cluster_export_service,
    router as export_service_router,
)
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
def service(dashboard: ClusterDashboardAPI) -> ClusterExportService:
    return ClusterExportService(dashboard)


@pytest.fixture
def client(service: ClusterExportService) -> TestClient:
    app = FastAPI()
    app.include_router(export_service_router)
    app.dependency_overrides[get_cluster_export_service] = lambda: service
    return TestClient(app)


def test_export_workers_json(registry: WorkerRegistry, service: ClusterExportService):
    registry.register("worker-1", ["parse"], make_metadata())

    export = service.export_workers(format="json")

    assert isinstance(export, ClusterExport)
    assert export.manifest.export_type == "workers"
    assert export.manifest.record_count == 1
    parsed = json.loads(export.content)
    assert parsed[0]["worker_id"] == "worker-1"


def test_export_workers_csv_has_header_and_row(registry: WorkerRegistry, service: ClusterExportService):
    registry.register("worker-1", ["parse"], make_metadata())

    export = service.export_workers(format="csv")

    lines = export.content.splitlines()
    assert "worker_id" in lines[0]
    assert "worker-1" in lines[1]


def test_export_workers_yaml_contains_worker_id(registry: WorkerRegistry, service: ClusterExportService):
    registry.register("worker-1", ["parse"], make_metadata())

    export = service.export_workers(format="yaml")

    assert "worker_id: worker-1" in export.content


def test_export_workers_rejects_unsupported_format(service: ClusterExportService):
    with pytest.raises(ValueError):
        service.export_workers(format="xml")


def test_export_executions_json(registry: WorkerRegistry, coordinator: ExecutionCoordinator, service: ClusterExportService):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    export = service.export_executions(format="json")

    assert export.manifest.export_type == "executions"
    parsed = json.loads(export.content)
    assert parsed[0]["job_id"] == "job-1"


def test_export_executions_filters_by_state(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, service: ClusterExportService
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.submit("job-2", "export")

    export = service.export_executions(format="json", state="queued")

    parsed = json.loads(export.content)
    assert [row["job_id"] for row in parsed] == ["job-2"]


def test_export_executions_csv_flattens_nested_history(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, service: ClusterExportService
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    export = service.export_executions(format="csv")

    assert "history" in export.content.splitlines()[0]


def test_export_analytics_scoped_to_capability(analytics: ClusterAnalyticsService, service: ClusterExportService):
    analytics.record("parse", worker_count=2, active_jobs=1, queue_depth=0)
    analytics.record("export", worker_count=1, active_jobs=1, queue_depth=0)

    export = service.export_analytics(format="json", capability="parse")

    parsed = json.loads(export.content)
    assert parsed["sample_count"] == 1
    assert export.manifest.record_count == 1


def test_export_analytics_overall_breakdown(analytics: ClusterAnalyticsService, service: ClusterExportService):
    analytics.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    export = service.export_analytics(format="json")

    parsed = json.loads(export.content)
    assert "overall" in parsed
    assert "by_capability" in parsed


def test_export_cluster_bundles_all_sections(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, analytics: ClusterAnalyticsService, service: ClusterExportService
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    analytics.record("parse", worker_count=1, active_jobs=1, queue_depth=0)

    export = service.export_cluster(format="json")

    parsed = json.loads(export.content)
    assert set(parsed.keys()) == {"overview", "workers", "executions", "analytics"}
    assert export.manifest.export_type == "cluster"


def test_manifest_checksum_reflects_content(registry: WorkerRegistry, service: ClusterExportService):
    registry.register("worker-1", ["parse"], make_metadata())

    export = service.export_workers(format="json")

    import hashlib

    assert export.manifest.checksum == hashlib.sha256(export.content.encode("utf-8")).hexdigest()


def test_api_export_workers_json(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/export/workers")

    assert response.status_code == 200
    assert response.json()["data"][0]["worker_id"] == "worker-1"


def test_api_export_workers_csv(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/export/workers", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "worker-1" in response.text


def test_api_export_workers_yaml(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/export/workers", params={"format": "yaml"})

    assert response.status_code == 200
    assert "application/x-yaml" in response.headers["content-type"]
    assert "worker_id: worker-1" in response.text


def test_api_export_workers_invalid_format_returns_422(client: TestClient):
    response = client.get("/cluster/export/workers", params={"format": "xml"})

    assert response.status_code == 422


def test_api_export_executions(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    response = client.get("/cluster/export/executions")

    assert response.status_code == 200
    assert response.json()["data"][0]["job_id"] == "job-1"


def test_api_export_analytics(client: TestClient, analytics: ClusterAnalyticsService):
    analytics.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    response = client.get("/cluster/export/analytics")

    assert response.status_code == 200
    assert "overall" in response.json()["data"]


def test_api_export_all(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/export/all")

    assert response.status_code == 200
    assert "workers" in response.json()["data"]
