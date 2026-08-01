import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import CacheManager, get_cache_manager, router as cache_manager_router
from backend.performance.in_memory_cache import InMemoryCache, get_in_memory_cache
from backend.performance.distributed_cache import (
    CacheBackend,
    ConnectionConfig,
    DistributedCacheAdapter,
    get_distributed_cache_adapter,
)
from backend.performance.cache_invalidation import (
    CacheInvalidationService,
    InvalidationRequest,
    InvalidationResult,
    InvalidationTrigger,
    get_cache_invalidation_service,
)


@pytest.fixture
def cache_manager() -> CacheManager:
    return CacheManager()


@pytest.fixture
def memory_cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def distributed_adapter() -> DistributedCacheAdapter:
    adapter = DistributedCacheAdapter()
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))
    return adapter


@pytest.fixture
def service(
    cache_manager: CacheManager, memory_cache: InMemoryCache, distributed_adapter: DistributedCacheAdapter
) -> CacheInvalidationService:
    return CacheInvalidationService(
        cache_manager=cache_manager,
        memory_cache=memory_cache,
        distributed_adapter=distributed_adapter,
    )


@pytest.fixture
def client(
    cache_manager: CacheManager,
    memory_cache: InMemoryCache,
    distributed_adapter: DistributedCacheAdapter,
    service: CacheInvalidationService,
) -> TestClient:
    app = FastAPI()
    app.include_router(cache_manager_router)
    app.dependency_overrides[get_cache_manager] = lambda: cache_manager
    app.dependency_overrides[get_in_memory_cache] = lambda: memory_cache
    app.dependency_overrides[get_distributed_cache_adapter] = lambda: distributed_adapter
    app.dependency_overrides[get_cache_invalidation_service] = lambda: service
    return TestClient(app)


def test_invalidation_request_to_dict():
    request = InvalidationRequest(key="a", namespace="ns")

    assert request.to_dict()["key"] == "a"
    assert request.to_dict()["trigger"] == "manual"


def test_invalidate_removes_from_cache_manager_and_memory_cache(
    service: CacheInvalidationService, cache_manager: CacheManager, memory_cache: InMemoryCache
):
    cache_manager.put("greeting", "hello")
    memory_cache.set("greeting", "hello")

    result = service.invalidate("greeting")

    assert isinstance(result, InvalidationResult)
    assert result.count == 1
    with pytest.raises(KeyError):
        cache_manager.get("greeting")
    assert memory_cache.contains("greeting") is False


def test_invalidate_missing_key_returns_zero_count(service: CacheInvalidationService):
    result = service.invalidate("missing")

    assert result.count == 0
    assert result.keys_invalidated == []


def test_invalidate_requires_key(service: CacheInvalidationService):
    with pytest.raises(ValueError):
        service.invalidate("")


def test_invalidate_propagates_to_distributed_backend(
    service: CacheInvalidationService, distributed_adapter: DistributedCacheAdapter
):
    distributed_adapter.set("greeting", "hello")

    result = service.invalidate("greeting")

    assert result.propagated is True
    assert result.backends_notified == ["primary"]
    with pytest.raises(KeyError):
        distributed_adapter.get("greeting")


def test_invalidate_pattern_matches_glob(
    service: CacheInvalidationService, cache_manager: CacheManager
):
    cache_manager.put("user:1", "a")
    cache_manager.put("user:2", "b")
    cache_manager.put("order:1", "c")

    result = service.invalidate_pattern("user:*")

    assert result.count == 2
    assert set(result.keys_invalidated) == {"user:1", "user:2"}
    with pytest.raises(KeyError):
        cache_manager.get("user:1")
    assert cache_manager.get("order:1").value == "c"


def test_invalidate_pattern_requires_pattern(service: CacheInvalidationService):
    with pytest.raises(ValueError):
        service.invalidate_pattern("")


def test_invalidate_pattern_matches_memory_cache(
    service: CacheInvalidationService, memory_cache: InMemoryCache
):
    memory_cache.set("session:1", "a")
    memory_cache.set("session:2", "b")

    result = service.invalidate_pattern("session:*")

    assert result.count == 2
    assert memory_cache.contains("session:1") is False


def test_invalidate_namespace_flushes_only_that_namespace(
    service: CacheInvalidationService, cache_manager: CacheManager
):
    cache_manager.put("a", 1, namespace="tenant-1")
    cache_manager.put("b", 2, namespace="tenant-1")
    cache_manager.put("c", 3, namespace="tenant-2")

    result = service.invalidate_namespace("tenant-1")

    assert result.trigger == InvalidationTrigger.NAMESPACE_FLUSH
    assert result.count == 2
    assert set(result.keys_invalidated) == {"a", "b"}
    assert cache_manager.get("c", namespace="tenant-2").value == 3


def test_invalidate_all_clears_local_caches_and_propagates(
    service: CacheInvalidationService,
    cache_manager: CacheManager,
    memory_cache: InMemoryCache,
    distributed_adapter: DistributedCacheAdapter,
):
    cache_manager.put("a", 1)
    memory_cache.set("b", 2)
    distributed_adapter.set("c", 3)

    result = service.invalidate_all()

    assert result.count == 2
    assert result.propagated is True
    assert distributed_adapter.list_backends()[0]["name"] == "primary"


def test_stats_tracks_totals_by_trigger(service: CacheInvalidationService, cache_manager: CacheManager):
    cache_manager.put("a", 1)

    service.invalidate("a", trigger=InvalidationTrigger.DATA_UPDATED)
    stats = service.stats()

    assert stats["total_invalidations"] == 1
    assert stats["by_trigger"]["data_updated"] == 1


def test_api_invalidate_key(client: TestClient, cache_manager: CacheManager):
    cache_manager.put("greeting", "hello")

    response = client.post("/performance/cache/invalidate", json={"key": "greeting"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    with pytest.raises(KeyError):
        cache_manager.get("greeting")


def test_api_invalidate_rejects_unknown_trigger(client: TestClient):
    response = client.post("/performance/cache/invalidate", json={"key": "a", "trigger": "not-a-trigger"})

    assert response.status_code == 422


def test_api_invalidate_pattern(client: TestClient, cache_manager: CacheManager):
    cache_manager.put("user:1", "a")
    cache_manager.put("user:2", "b")

    response = client.post("/performance/cache/invalidate-pattern", json={"pattern": "user:*"})

    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_api_invalidate_namespace(client: TestClient, cache_manager: CacheManager):
    cache_manager.put("a", 1, namespace="tenant-1")
    cache_manager.put("b", 2, namespace="tenant-2")

    response = client.delete("/performance/cache/namespace/tenant-1")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["trigger"] == "namespace_flush"
    assert cache_manager.get("b", namespace="tenant-2").value == 2
