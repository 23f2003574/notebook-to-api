import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import router as cache_manager_router
from backend.performance.in_memory_cache import InMemoryCache, get_in_memory_cache
from backend.performance.cache_eviction import (
    CacheEvictionEngine,
    EvictionPolicy,
    EvictionResult,
    get_cache_eviction_engine,
)


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def engine() -> CacheEvictionEngine:
    return CacheEvictionEngine()


@pytest.fixture
def client(cache: InMemoryCache, engine: CacheEvictionEngine) -> TestClient:
    app = FastAPI()
    app.include_router(cache_manager_router)
    app.dependency_overrides[get_in_memory_cache] = lambda: cache
    app.dependency_overrides[get_cache_eviction_engine] = lambda: engine
    return TestClient(app)


def test_configure_rejects_non_positive_max_entries(engine: CacheEvictionEngine):
    with pytest.raises(ValueError):
        engine.configure(policy=EvictionPolicy.LRU, max_entries=0)


def test_configure_rejects_non_positive_max_memory(engine: CacheEvictionEngine):
    with pytest.raises(ValueError):
        engine.configure(policy=EvictionPolicy.LRU, max_memory_bytes=-1)


def test_lru_select_picks_least_recently_used(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    cache.get("a")
    cache.get("c")
    engine.configure(policy=EvictionPolicy.LRU)

    selected = engine.select(cache, count=1)

    assert selected == ["b"]


def test_lru_evict_removes_least_recently_used(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    engine.configure(policy=EvictionPolicy.LRU)

    result = engine.evict(cache, count=1)

    assert isinstance(result, EvictionResult)
    assert result.evicted_keys == ["b"]
    assert cache.contains("a") is True
    assert cache.contains("b") is False


def test_lfu_evict_removes_least_frequently_used(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    cache.get("a")
    cache.get("b")
    engine.configure(policy=EvictionPolicy.LFU)

    result = engine.evict(cache, count=1)

    assert result.evicted_keys == ["b"]
    assert cache.contains("a") is True


def test_fifo_evict_removes_oldest_inserted(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("first", 1)
    time.sleep(0.005)
    cache.set("second", 2)
    cache.get("first")
    engine.configure(policy=EvictionPolicy.FIFO)

    result = engine.evict(cache, count=1)

    assert result.evicted_keys == ["first"]


def test_ttl_cleanup_removes_expired_entries_regardless_of_policy(
    cache: InMemoryCache, engine: CacheEvictionEngine
):
    cache.set("expiring", "value", ttl_seconds=0.01)
    cache.set("permanent", "value")
    time.sleep(0.02)
    engine.configure(policy=EvictionPolicy.LRU)

    result = engine.evict(cache)

    assert result.reason == "ttl"
    assert "expiring" in result.evicted_keys
    assert cache.contains("permanent") is True


def test_ttl_policy_select_orders_by_soonest_expiry(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("soon", "value", ttl_seconds=100)
    cache.set("later", "value", ttl_seconds=1000)
    engine.configure(policy=EvictionPolicy.TTL)

    selected = engine.select(cache, count=1)

    assert selected == ["soon"]


def test_threshold_eviction_by_max_entries(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    engine.configure(policy=EvictionPolicy.FIFO, max_entries=1)

    result = engine.evict(cache)

    assert result.reason == "threshold"
    assert cache.stats().entries == 1
    assert cache.contains("c") is True


def test_threshold_eviction_by_max_memory(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", "x" * 100)
    cache.set("b", "y" * 100)
    size_of_one = cache.stats().memory_bytes // 2
    engine.configure(policy=EvictionPolicy.FIFO, max_memory_bytes=size_of_one + 10)

    result = engine.evict(cache)

    assert result.reason == "threshold"
    assert cache.stats().memory_bytes <= size_of_one + 10


def test_evict_with_no_thresholds_only_cleans_expired(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", 1)

    result = engine.evict(cache)

    assert result.evicted_keys == []
    assert cache.contains("a") is True


def test_stats_tracks_totals_by_policy(cache: InMemoryCache, engine: CacheEvictionEngine):
    cache.set("a", 1)
    cache.set("b", 2)
    engine.configure(policy=EvictionPolicy.LRU)

    engine.evict(cache, count=1)
    stats = engine.stats()

    assert stats["total_evictions"] == 1
    assert stats["evictions_by_policy"]["lru"] == 1
    assert stats["runs"] == 1


def test_api_configure_eviction_and_run(client: TestClient, cache: InMemoryCache):
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)

    response = client.post(
        "/performance/cache/eviction",
        json={"policy": "fifo", "max_entries": 1},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["policy"] == "fifo"
    assert body["reason"] == "threshold"
    assert cache.stats().entries == 1


def test_api_configure_eviction_rejects_unknown_policy(client: TestClient):
    response = client.post("/performance/cache/eviction", json={"policy": "not-a-policy"})

    assert response.status_code == 422


def test_api_get_eviction_config(client: TestClient):
    client.post("/performance/cache/eviction", json={"policy": "lfu", "max_entries": 5})

    response = client.get("/performance/cache/eviction")

    assert response.status_code == 200
    body = response.json()
    assert body["policy"] == "lfu"
    assert body["max_entries"] == 5


def test_api_eviction_stats(client: TestClient, cache: InMemoryCache):
    cache.set("a", 1)
    cache.set("b", 2)
    client.post("/performance/cache/eviction", json={"policy": "fifo", "max_entries": 1})

    response = client.get("/performance/cache/eviction/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["total_evictions"] == 1
    assert body["runs"] == 1
