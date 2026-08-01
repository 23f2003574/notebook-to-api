import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.profiler import pool_router
from backend.performance.resource_pool import (
    PoolAlreadyExistsError,
    PoolExhaustedError,
    PoolType,
    PooledResource,
    ResourceNotAcquiredError,
    ResourcePool,
    ResourcePoolManager,
    UnknownPoolError,
    UnknownResourceError,
    get_resource_pool_manager,
)


@pytest.fixture
def manager() -> ResourcePoolManager:
    return ResourcePoolManager()


@pytest.fixture
def client(manager: ResourcePoolManager) -> TestClient:
    app = FastAPI()
    app.include_router(pool_router)
    app.dependency_overrides[get_resource_pool_manager] = lambda: manager
    return TestClient(app)


def test_create_pool_returns_pool_config(manager: ResourcePoolManager):
    pool = manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=5)

    assert isinstance(pool, ResourcePool)
    assert pool.name == "workers"
    assert pool.pool_type == PoolType.WORKER
    assert pool.max_size == 5


def test_create_pool_rejects_duplicate_name(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=5)

    with pytest.raises(PoolAlreadyExistsError):
        manager.create_pool("workers", pool_type=PoolType.THREAD, max_size=3)


def test_create_pool_rejects_invalid_sizes(manager: ResourcePoolManager):
    with pytest.raises(ValueError):
        manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=0)
    with pytest.raises(ValueError):
        manager.create_pool("workers", pool_type=PoolType.WORKER, min_size=5, max_size=2)


def test_create_pool_pre_warms_min_size(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, min_size=2, max_size=5)

    stats = manager.stats("workers")
    assert stats["size"] == 2
    assert stats["available"] == 2


def test_acquire_returns_pooled_resource(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=2)

    resource = manager.acquire("workers")

    assert isinstance(resource, PooledResource)
    assert resource.in_use is True
    assert resource.use_count == 1


def test_acquire_reuses_released_resource(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=1)
    first = manager.acquire("workers")
    manager.release("workers", first.resource_id)

    second = manager.acquire("workers")

    assert second.resource_id == first.resource_id
    assert second.use_count == 2


def test_acquire_creates_new_resource_up_to_max(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=2)

    first = manager.acquire("workers")
    second = manager.acquire("workers")

    assert first.resource_id != second.resource_id
    assert manager.stats("workers")["size"] == 2


def test_acquire_exhausted_pool_raises(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=1)
    manager.acquire("workers")

    with pytest.raises(PoolExhaustedError):
        manager.acquire("workers")


def test_acquire_unknown_pool_raises(manager: ResourcePoolManager):
    with pytest.raises(UnknownPoolError):
        manager.acquire("missing")


def test_release_unknown_resource_raises(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=1)

    with pytest.raises(UnknownResourceError):
        manager.release("workers", "missing")


def test_release_not_acquired_raises(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, min_size=1, max_size=1)
    resources = manager.stats("workers")
    resource_id = "workers-1"

    with pytest.raises(ResourceNotAcquiredError):
        manager.release("workers", resource_id)


def test_resize_grows_pool_to_new_min(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=5)

    manager.resize("workers", min_size=3)

    assert manager.stats("workers")["size"] == 3


def test_resize_shrinks_idle_resources_to_new_max(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, min_size=3, max_size=5)

    manager.resize("workers", min_size=1, max_size=1)

    stats = manager.stats("workers")
    assert stats["size"] == 1
    assert stats["max_size"] == 1


def test_resize_does_not_evict_in_use_resources(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=2)
    acquired = manager.acquire("workers")
    manager.acquire("workers")

    manager.resize("workers", max_size=1)

    stats = manager.stats("workers")
    assert stats["size"] >= 1
    assert acquired.in_use is True


def test_resize_rejects_invalid_bounds(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=5)

    with pytest.raises(ValueError):
        manager.resize("workers", min_size=10, max_size=5)


def test_resize_unknown_pool_raises(manager: ResourcePoolManager):
    with pytest.raises(UnknownPoolError):
        manager.resize("missing", max_size=5)


def test_cleanup_idle_removes_expired_resources(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=5, idle_timeout_seconds=0.01)
    resource = manager.acquire("workers")
    manager.release("workers", resource.resource_id)
    time.sleep(0.02)

    removed = manager.cleanup_idle("workers")

    assert removed == 1
    assert manager.stats("workers")["size"] == 0


def test_cleanup_idle_respects_min_size_floor(manager: ResourcePoolManager):
    manager.create_pool(
        "workers", pool_type=PoolType.WORKER, min_size=2, max_size=5, idle_timeout_seconds=0.01
    )
    time.sleep(0.02)

    removed = manager.cleanup_idle("workers")

    assert removed == 0
    assert manager.stats("workers")["size"] == 2


def test_cleanup_idle_without_timeout_configured_is_noop(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, min_size=1, max_size=5)
    time.sleep(0.01)

    removed = manager.cleanup_idle("workers")

    assert removed == 0


def test_stats_reports_utilization(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=4)
    manager.acquire("workers")

    stats = manager.stats("workers")

    assert stats["in_use"] == 1
    assert stats["available"] == 0
    assert stats["utilization_percent"] == 25.0


def test_stats_unknown_pool_raises(manager: ResourcePoolManager):
    with pytest.raises(UnknownPoolError):
        manager.stats("missing")


def test_list_pools_returns_all(manager: ResourcePoolManager):
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=2)
    manager.create_pool("buffers", pool_type=PoolType.BUFFER, max_size=2)

    pools = manager.list_pools()

    assert {p.name for p in pools} == {"workers", "buffers"}


def test_api_create_pool(client: TestClient):
    response = client.post(
        "/performance/pools",
        json={"name": "workers", "pool_type": "worker", "min_size": 1, "max_size": 5},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "workers"
    assert body["pool_type"] == "worker"


def test_api_create_pool_rejects_unknown_type(client: TestClient):
    response = client.post("/performance/pools", json={"name": "workers", "pool_type": "gpu"})

    assert response.status_code == 422


def test_api_create_pool_duplicate_returns_409(client: TestClient):
    client.post("/performance/pools", json={"name": "workers", "pool_type": "worker", "max_size": 2})

    response = client.post("/performance/pools", json={"name": "workers", "pool_type": "worker", "max_size": 2})

    assert response.status_code == 409


def test_api_list_pools(client: TestClient):
    client.post("/performance/pools", json={"name": "workers", "pool_type": "worker", "max_size": 2})

    response = client.get("/performance/pools")

    assert response.status_code == 200
    assert len(response.json()["pools"]) == 1


def test_api_pool_stats(client: TestClient, manager: ResourcePoolManager):
    client.post("/performance/pools", json={"name": "workers", "pool_type": "worker", "max_size": 2})
    manager.acquire("workers")

    response = client.get("/performance/pools/workers/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["in_use"] == 1
    assert body["size"] == 1


def test_api_pool_stats_unknown_pool_returns_404(client: TestClient):
    response = client.get("/performance/pools/missing/stats")

    assert response.status_code == 404
