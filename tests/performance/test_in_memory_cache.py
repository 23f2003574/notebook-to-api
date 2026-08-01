import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import router as cache_manager_router
from backend.performance.in_memory_cache import (
    CacheNode,
    InMemoryCache,
    MemoryCacheStats,
    get_in_memory_cache,
)


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def client(cache: InMemoryCache) -> TestClient:
    app = FastAPI()
    app.include_router(cache_manager_router)
    app.dependency_overrides[get_in_memory_cache] = lambda: cache
    return TestClient(app)


def test_set_creates_cache_node(cache: InMemoryCache):
    node = cache.set("greeting", "hello")

    assert isinstance(node, CacheNode)
    assert node.key == "greeting"
    assert node.value == "hello"
    assert node.size_bytes > 0


def test_set_rejects_empty_key(cache: InMemoryCache):
    with pytest.raises(ValueError):
        cache.set("", "value")


def test_set_rejects_non_positive_ttl(cache: InMemoryCache):
    with pytest.raises(ValueError):
        cache.set("greeting", "hello", ttl_seconds=0)


def test_get_returns_stored_value(cache: InMemoryCache):
    cache.set("greeting", "hello")

    node = cache.get("greeting")

    assert node.value == "hello"


def test_get_missing_key_raises(cache: InMemoryCache):
    with pytest.raises(KeyError):
        cache.get("missing")


def test_contains_reflects_presence(cache: InMemoryCache):
    assert cache.contains("greeting") is False

    cache.set("greeting", "hello")

    assert cache.contains("greeting") is True


def test_node_expires_after_ttl(cache: InMemoryCache):
    cache.set("greeting", "hello", ttl_seconds=0.01)
    time.sleep(0.02)

    with pytest.raises(KeyError):
        cache.get("greeting")
    assert cache.contains("greeting") is False


def test_remove_deletes_entry(cache: InMemoryCache):
    cache.set("greeting", "hello")

    cache.remove("greeting")

    with pytest.raises(KeyError):
        cache.get("greeting")


def test_remove_missing_key_raises(cache: InMemoryCache):
    with pytest.raises(KeyError):
        cache.remove("missing")


def test_clear_removes_all_entries(cache: InMemoryCache):
    cache.set("a", 1)
    cache.set("b", 2)

    removed = cache.clear()

    assert removed == 2
    assert cache.stats().entries == 0


def test_stats_reports_entries_memory_hits_and_misses(cache: InMemoryCache):
    cache.set("a", "value")
    cache.get("a")
    with pytest.raises(KeyError):
        cache.get("missing")

    stats = cache.stats()

    assert isinstance(stats, MemoryCacheStats)
    assert stats.entries == 1
    assert stats.memory_bytes > 0
    assert stats.hits == 1
    assert stats.misses == 1


def test_concurrent_set_and_get_is_thread_safe(cache: InMemoryCache):
    def worker(i: int) -> None:
        cache.set(f"key-{i}", i)
        assert cache.get(f"key-{i}").value == i

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(worker, range(200)))

    assert cache.stats().entries == 200


def test_api_memory_stats(client: TestClient, cache: InMemoryCache):
    cache.set("a", "value")

    response = client.get("/performance/cache/memory/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == 1
    assert body["memory_bytes"] > 0


def test_api_memory_clear(client: TestClient, cache: InMemoryCache):
    cache.set("a", "value")

    response = client.post("/performance/cache/memory/clear")
    assert response.status_code == 204

    assert cache.stats().entries == 0
