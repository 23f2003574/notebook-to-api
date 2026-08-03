import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.worker_registry import (
    WorkerMetadata,
    WorkerNode,
    WorkerRegistry,
    get_worker_registry,
    router as worker_registry_router,
)


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0", tags=("gpu",))


@pytest.fixture
def registry() -> WorkerRegistry:
    return WorkerRegistry()


@pytest.fixture
def client(registry: WorkerRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(worker_registry_router)
    app.dependency_overrides[get_worker_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_worker(registry: WorkerRegistry):
    node = registry.register("worker-1", ["parse", "export"], make_metadata())

    assert isinstance(node, WorkerNode)
    assert node.worker_id == "worker-1"
    assert node.capabilities == ("parse", "export")
    assert node.status == "online"


def test_register_rejects_empty_worker_id(registry: WorkerRegistry):
    with pytest.raises(ValueError):
        registry.register("", ["parse"], make_metadata())


def test_register_rejects_invalid_status(registry: WorkerRegistry):
    with pytest.raises(ValueError):
        registry.register("worker-1", ["parse"], make_metadata(), status="hibernating")


def test_register_overwrites_existing_worker_and_reindexes(registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-1", ["export"], make_metadata())

    assert registry.list_workers(capability="parse") == []
    assert len(registry.list_workers(capability="export")) == 1


def test_get_returns_registered_worker(registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    node = registry.get("worker-1")

    assert node is not None
    assert node.worker_id == "worker-1"


def test_get_returns_none_for_missing_worker(registry: WorkerRegistry):
    assert registry.get("does-not-exist") is None


def test_list_workers_returns_sorted_by_id(registry: WorkerRegistry):
    registry.register("worker-b", ["parse"], make_metadata())
    registry.register("worker-a", ["parse"], make_metadata())

    workers = registry.list_workers()

    assert [worker.worker_id for worker in workers] == ["worker-a", "worker-b"]


def test_list_workers_filters_by_status(registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["parse"], make_metadata(), status="draining")

    online = registry.list_workers(status="online")

    assert [worker.worker_id for worker in online] == ["worker-1"]


def test_list_workers_filters_by_capability(registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["export"], make_metadata())

    parsers = registry.list_workers(capability="parse")

    assert [worker.worker_id for worker in parsers] == ["worker-1"]


def test_unregister_removes_worker(registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    removed = registry.unregister("worker-1")

    assert removed is True
    assert registry.get("worker-1") is None
    assert registry.list_workers(capability="parse") == []


def test_unregister_missing_worker_returns_false(registry: WorkerRegistry):
    assert registry.unregister("does-not-exist") is False


def test_set_status_updates_worker(registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    node = registry.set_status("worker-1", "draining")

    assert node.status == "draining"


def test_set_status_missing_worker_raises(registry: WorkerRegistry):
    with pytest.raises(KeyError):
        registry.set_status("does-not-exist", "draining")


def test_api_register_worker(client: TestClient):
    response = client.post(
        "/cluster/workers",
        json={
            "worker_id": "worker-1",
            "capabilities": ["parse", "export"],
            "metadata": {"hostname": "node-a.local", "region": "us-east-1", "version": "1.0.0"},
        },
    )

    assert response.status_code == 200
    assert response.json()["worker_id"] == "worker-1"


def test_api_register_worker_missing_field_returns_422(client: TestClient):
    response = client.post("/cluster/workers", json={"capabilities": ["parse"]})

    assert response.status_code == 422


def test_api_list_workers(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/workers")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_worker(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/workers/worker-1")

    assert response.status_code == 200
    assert response.json()["worker_id"] == "worker-1"


def test_api_get_worker_not_found(client: TestClient):
    response = client.get("/cluster/workers/does-not-exist")

    assert response.status_code == 404


def test_api_delete_worker(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.delete("/cluster/workers/worker-1")

    assert response.status_code == 200
    assert registry.get("worker-1") is None


def test_api_delete_worker_not_found(client: TestClient):
    response = client.delete("/cluster/workers/does-not-exist")

    assert response.status_code == 404
