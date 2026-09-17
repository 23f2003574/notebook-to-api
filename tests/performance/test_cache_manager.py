import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import (
    CacheEntry,
    CacheKeyError,
    CacheManager,
    CacheStats,
    get_cache_manager,
    router as cache_manager_router,
)


@pytest.fixture
def cache() -> CacheManager:
    return CacheManager()


@pytest.fixture
def client(cache: CacheManager) -> TestClient:
    app = FastAPI()
    app.include_router(cache_manager_router)
    app.dependency_overrides[get_cache_manager] = lambda: cache
    return TestClient(app)


def test_put_creates_cache_entry(cache: CacheManager):
    entry = cache.put("greeting", "hello")

    assert isinstance(entry, CacheEntry)
    assert entry.key == "greeting"
    assert entry.value == "hello"
    assert entry.namespace == "default"


def test_put_rejects_empty_key(cache: CacheManager):
    with pytest.raises(ValueError):
        cache.put("", "value")


def test_put_rejects_non_positive_ttl(cache: CacheManager):
    with pytest.raises(ValueError):
        cache.put("greeting", "hello", ttl_seconds=0)


def test_get_returns_stored_value(cache: CacheManager):
    cache.put("greeting", "hello")

    entry = cache.get("greeting")

    assert entry.value == "hello"


def test_get_missing_key_raises(cache: CacheManager):
    with pytest.raises(CacheKeyError):
        cache.get("missing")


def test_namespace_isolation(cache: CacheManager):
    cache.put("key", "a", namespace="ns1")
    cache.put("key", "b", namespace="ns2")

    assert cache.get("key", namespace="ns1").value == "a"
    assert cache.get("key", namespace="ns2").value == "b"

    with pytest.raises(CacheKeyError):
        cache.get("key")


def test_entry_expires_after_ttl(cache: CacheManager):
    cache.put("greeting", "hello", ttl_seconds=0.01)
    time.sleep(0.02)

    with pytest.raises(CacheKeyError):
        cache.get("greeting")


def test_delete_removes_entry(cache: CacheManager):
    cache.put("greeting", "hello")

    cache.delete("greeting")

    with pytest.raises(CacheKeyError):
        cache.get("greeting")


def test_delete_missing_key_raises(cache: CacheManager):
    with pytest.raises(CacheKeyError):
        cache.delete("missing")


def test_clear_removes_all_entries(cache: CacheManager):
    cache.put("a", 1)
    cache.put("b", 2)

    removed = cache.clear()

    assert removed == 2
    assert cache.stats().size == 0


def test_clear_scoped_to_namespace(cache: CacheManager):
    cache.put("key", "a", namespace="ns1")
    cache.put("key", "b", namespace="ns2")

    removed = cache.clear(namespace="ns1")

    assert removed == 1
    assert cache.get("key", namespace="ns2").value == "b"
    with pytest.raises(CacheKeyError):
        cache.get("key", namespace="ns1")


def test_stats_reports_hits_misses_and_evictions(cache: CacheManager):
    cache.put("greeting", "hello", ttl_seconds=0.01)
    cache.get("greeting")
    time.sleep(0.02)
    with pytest.raises(CacheKeyError):
        cache.get("greeting")
    with pytest.raises(CacheKeyError):
        cache.get("missing")

    stats = cache.stats()

    assert isinstance(stats, CacheStats)
    assert stats.hits == 1
    assert stats.misses == 2
    assert stats.evictions == 1
    assert stats.size == 0


def test_api_put_and_get(client: TestClient):
    response = client.post("/performance/cache", json={"key": "greeting", "value": "hello"})
    assert response.status_code == 201
    assert response.json()["value"] == "hello"

    fetched = client.get("/performance/cache/greeting")
    assert fetched.status_code == 200
    assert fetched.json()["value"] == "hello"


def test_api_get_missing_key_returns_404(client: TestClient):
    response = client.get("/performance/cache/missing")

    assert response.status_code == 404


def test_api_delete_key(client: TestClient):
    client.post("/performance/cache", json={"key": "greeting", "value": "hello"})

    response = client.delete("/performance/cache/greeting")
    assert response.status_code == 204

    assert client.get("/performance/cache/greeting").status_code == 404


def test_api_clear_cache(client: TestClient):
    client.post("/performance/cache", json={"key": "a", "value": 1})
    client.post("/performance/cache", json={"key": "b", "value": 2})

    response = client.delete("/performance/cache")
    assert response.status_code == 204

    assert client.get("/performance/cache/a").status_code == 404
    assert client.get("/performance/cache/b").status_code == 404
