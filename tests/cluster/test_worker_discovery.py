from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.worker_discovery import (
    DiscoveryRecord,
    HeartbeatStatus,
    WorkerDiscoveryService,
    get_worker_discovery_service,
    router as worker_discovery_router,
)
from backend.cluster.worker_registry import WorkerMetadata, WorkerRegistry


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0")


@pytest.fixture
def registry() -> WorkerRegistry:
    return WorkerRegistry()


@pytest.fixture
def service(registry: WorkerRegistry) -> WorkerDiscoveryService:
    return WorkerDiscoveryService(registry, stale_after_seconds=30.0)


@pytest.fixture
def client(service: WorkerDiscoveryService) -> TestClient:
    app = FastAPI()
    app.include_router(worker_discovery_router)
    app.dependency_overrides[get_worker_discovery_service] = lambda: service
    return TestClient(app)


def test_discover_returns_record_per_worker(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata())

    records = service.discover()

    assert len(records) == 1
    assert isinstance(records[0], DiscoveryRecord)
    assert records[0].worker_id == "worker-1"
    assert records[0].is_stale is False


def test_discover_filters_by_capability(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["export"], make_metadata())

    records = service.discover(capability="parse")

    assert [record.worker_id for record in records] == ["worker-1"]


def test_discover_flags_stale_worker(registry: WorkerRegistry, service: WorkerDiscoveryService):
    node = registry.register("worker-1", ["parse"], make_metadata())
    node.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    records = service.discover()

    assert records[0].is_stale is True
    assert records[0].seconds_since_heartbeat >= 60


def test_heartbeat_updates_last_seen_at(registry: WorkerRegistry, service: WorkerDiscoveryService):
    node = registry.register("worker-1", ["parse"], make_metadata())
    node.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    status = service.heartbeat("worker-1")

    assert isinstance(status, HeartbeatStatus)
    assert status.worker_id == "worker-1"
    refreshed = registry.get("worker-1")
    assert (datetime.now(timezone.utc) - refreshed.last_seen_at).total_seconds() < 5


def test_heartbeat_missing_worker_raises(service: WorkerDiscoveryService):
    with pytest.raises(KeyError):
        service.heartbeat("does-not-exist")


def test_refresh_marks_stale_workers_offline(registry: WorkerRegistry, service: WorkerDiscoveryService):
    node = registry.register("worker-1", ["parse"], make_metadata())
    node.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    marked = service.refresh()

    assert marked == ["worker-1"]
    assert registry.get("worker-1").status == "offline"


def test_refresh_leaves_fresh_workers_untouched(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata())

    marked = service.refresh()

    assert marked == []
    assert registry.get("worker-1").status == "online"


def test_refresh_ignores_already_offline_workers(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata(), status="offline")

    marked = service.refresh()

    assert marked == []


def test_available_workers_excludes_stale(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata())
    stale_node = registry.register("worker-2", ["parse"], make_metadata())
    stale_node.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    available = service.available_workers()

    assert [worker.worker_id for worker in available] == ["worker-1"]


def test_available_workers_filters_by_capability(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["export"], make_metadata())

    available = service.available_workers(capability="export")

    assert [worker.worker_id for worker in available] == ["worker-2"]


def test_available_workers_excludes_draining(registry: WorkerRegistry, service: WorkerDiscoveryService):
    registry.register("worker-1", ["parse"], make_metadata(), status="draining")

    available = service.available_workers()

    assert available == []


def test_api_heartbeat(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/discovery/heartbeat", json={"worker_id": "worker-1"})

    assert response.status_code == 200
    assert response.json()["worker_id"] == "worker-1"


def test_api_heartbeat_missing_worker_id_returns_422(client: TestClient):
    response = client.post("/cluster/discovery/heartbeat", json={})

    assert response.status_code == 422


def test_api_heartbeat_unknown_worker_returns_404(client: TestClient):
    response = client.post("/cluster/discovery/heartbeat", json={"worker_id": "does-not-exist"})

    assert response.status_code == 404


def test_api_discover(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/discovery")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_available_workers(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/discovery/available", params={"capability": "parse"})

    assert response.status_code == 200
    assert len(response.json()) == 1
