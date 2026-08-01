import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import router as cache_manager_router
from backend.performance.distributed_cache import (
    CacheBackend,
    ConnectionConfig,
    DistributedCacheAdapter,
    NoAvailableBackendError,
    UnknownBackendError,
    get_distributed_cache_adapter,
)


@pytest.fixture
def adapter() -> DistributedCacheAdapter:
    return DistributedCacheAdapter()


@pytest.fixture
def client(adapter: DistributedCacheAdapter) -> TestClient:
    app = FastAPI()
    app.include_router(cache_manager_router)
    app.dependency_overrides[get_distributed_cache_adapter] = lambda: adapter
    return TestClient(app)


def test_connect_registers_backend(adapter: DistributedCacheAdapter):
    config = ConnectionConfig(name="primary", backend=CacheBackend.REDIS)

    connection = adapter.connect(config)

    assert connection.connected is True
    assert connection.config.name == "primary"


def test_set_and_get_round_trip(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))

    adapter.set("greeting", "hello")

    assert adapter.get("greeting") == "hello"


def test_get_missing_key_raises(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))

    with pytest.raises(KeyError):
        adapter.get("missing")


def test_delete_removes_value(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))
    adapter.set("greeting", "hello")

    adapter.delete("greeting")

    with pytest.raises(KeyError):
        adapter.get("greeting")


def test_operations_without_backend_raise(adapter: DistributedCacheAdapter):
    with pytest.raises(NoAvailableBackendError):
        adapter.set("greeting", "hello")


def test_failover_uses_next_healthy_backend_by_priority(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS, priority=0))
    adapter.connect(ConnectionConfig(name="secondary", backend=CacheBackend.MEMCACHED, priority=1))

    adapter.set_backend_health("primary", healthy=False)
    name = adapter.set("greeting", "hello")

    assert name == "secondary"
    assert adapter.get("greeting") == "hello"


def test_failover_skips_disconnected_backend(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS, priority=0))
    adapter.connect(ConnectionConfig(name="secondary", backend=CacheBackend.CUSTOM, priority=1))

    adapter.disconnect("primary")
    name = adapter.set("greeting", "hello")

    assert name == "secondary"


def test_all_backends_down_raises(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))
    adapter.set_backend_health("primary", healthy=False)

    with pytest.raises(NoAvailableBackendError):
        adapter.set("greeting", "hello")


def test_set_backend_health_unknown_backend_raises(adapter: DistributedCacheAdapter):
    with pytest.raises(UnknownBackendError):
        adapter.set_backend_health("missing", healthy=False)


def test_health_check_reports_backend_status(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))
    adapter.connect(ConnectionConfig(name="secondary", backend=CacheBackend.MEMCACHED))
    adapter.set_backend_health("secondary", healthy=False)

    report = adapter.health_check()

    statuses = {entry["name"]: entry["healthy"] for entry in report}
    assert statuses == {"primary": True, "secondary": False}


def test_list_backends_orders_by_priority(adapter: DistributedCacheAdapter):
    adapter.connect(ConnectionConfig(name="low", backend=CacheBackend.REDIS, priority=5))
    adapter.connect(ConnectionConfig(name="high", backend=CacheBackend.REDIS, priority=0))

    names = [entry["name"] for entry in adapter.list_backends()]

    assert names == ["high", "low"]


def test_api_connect_backend(client: TestClient):
    response = client.post(
        "/performance/cache/backend",
        json={"name": "primary", "backend": "redis", "host": "cache.internal", "port": 6379},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "primary"
    assert body["backend"] == "redis"
    assert body["connected"] is True


def test_api_connect_backend_rejects_unknown_type(client: TestClient):
    response = client.post(
        "/performance/cache/backend",
        json={"name": "primary", "backend": "not-a-real-backend"},
    )

    assert response.status_code == 422


def test_api_list_backends(client: TestClient):
    client.post("/performance/cache/backend", json={"name": "primary", "backend": "redis"})

    response = client.get("/performance/cache/backend")

    assert response.status_code == 200
    assert len(response.json()["backends"]) == 1


def test_api_backend_health(client: TestClient, adapter: DistributedCacheAdapter):
    client.post("/performance/cache/backend", json={"name": "primary", "backend": "memcached"})
    adapter.set_backend_health("primary", healthy=False)

    response = client.get("/performance/cache/backend/health")

    assert response.status_code == 200
    assert response.json()["backends"][0]["healthy"] is False
