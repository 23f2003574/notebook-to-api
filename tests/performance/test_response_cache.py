import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import router as cache_manager_router
from backend.performance.response_cache import (
    CacheContext,
    CachedResponse,
    ResponseCacheMiddleware,
    get_response_cache_middleware,
)
from backend.gateway.middleware import MiddlewareContext, MiddlewarePipeline, BUILTIN_MIDDLEWARE_FACTORIES


@pytest.fixture
def cache() -> ResponseCacheMiddleware:
    return ResponseCacheMiddleware()


@pytest.fixture
def client(cache: ResponseCacheMiddleware) -> TestClient:
    app = FastAPI()
    app.include_router(cache_manager_router)
    app.dependency_overrides[get_response_cache_middleware] = lambda: cache
    return TestClient(app)


def test_cache_key_is_deterministic_regardless_of_query_order(cache: ResponseCacheMiddleware):
    key1 = cache.cache_key("GET", "/notebooks", {"b": "2", "a": "1"})
    key2 = cache.cache_key("get", "/notebooks", {"a": "1", "b": "2"})

    assert key1 == key2


def test_before_request_misses_when_nothing_cached(cache: ResponseCacheMiddleware):
    result = cache.before_request(CacheContext(method="GET", path="/notebooks"))

    assert result is None


def test_after_response_caches_get_responses(cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))

    cached = cache.before_request(CacheContext(method="GET", path="/notebooks"))

    assert isinstance(cached, CachedResponse)
    assert cached.body == {"ok": True}


def test_after_response_ignores_non_get_methods(cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="POST", path="/notebooks", status_code=200, body={"ok": True}))

    assert cache.before_request(CacheContext(method="POST", path="/notebooks")) is None
    assert cache.stats()["size"] == 0


def test_after_response_ignores_error_responses(cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=500, body={"error": True}))

    assert cache.stats()["size"] == 0


def test_after_response_rejects_non_positive_ttl(cache: ResponseCacheMiddleware):
    with pytest.raises(ValueError):
        cache.after_response(
            CacheContext(method="GET", path="/notebooks", status_code=200, body={}, ttl_seconds=0)
        )


def test_cache_hit_and_miss_tracking(cache: ResponseCacheMiddleware):
    cache.before_request(CacheContext(method="GET", path="/notebooks"))
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))
    cache.before_request(CacheContext(method="GET", path="/notebooks"))

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_response_expires_after_ttl(cache: ResponseCacheMiddleware):
    cache.after_response(
        CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}, ttl_seconds=0.01)
    )
    time.sleep(0.02)

    assert cache.before_request(CacheContext(method="GET", path="/notebooks")) is None


def test_invalidate_by_key(cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))
    key = cache.cache_key("GET", "/notebooks")

    removed = cache.invalidate(key=key)

    assert removed == 1
    assert cache.before_request(CacheContext(method="GET", path="/notebooks")) is None


def test_invalidate_by_path_removes_all_query_variants(cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body=1))
    cache.after_response(
        CacheContext(method="GET", path="/notebooks", query_params={"page": "2"}, status_code=200, body=2)
    )

    removed = cache.invalidate(path="/notebooks")

    assert removed == 2
    assert cache.stats()["size"] == 0


def test_invalidate_without_args_clears_everything(cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/a", status_code=200, body=1))
    cache.after_response(CacheContext(method="GET", path="/b", status_code=200, body=2))

    removed = cache.invalidate()

    assert removed == 2
    assert cache.stats()["size"] == 0


def test_invalidate_missing_key_returns_zero(cache: ResponseCacheMiddleware):
    assert cache.invalidate(key="missing") == 0


def test_middleware_pipeline_serves_cached_response_on_second_call(cache: ResponseCacheMiddleware):
    pipeline = MiddlewarePipeline()
    before, after = BUILTIN_MIDDLEWARE_FACTORIES["response_caching"]({})
    pipeline.register("response_caching", before=before, after=after)

    import backend.gateway.middleware as middleware_module
    original_getter = middleware_module.get_response_cache_middleware
    middleware_module.get_response_cache_middleware = lambda: cache
    try:
        first = MiddlewareContext(path="/notebooks", method="GET", payload={"status_code": 200})
        pipeline.execute_before(first)
        first.response = {"data": "fresh"}
        pipeline.execute_after(first)

        second = MiddlewareContext(path="/notebooks", method="GET", payload={"status_code": 200})
        pipeline.execute_before(second)

        assert second.short_circuited is True
        assert second.response == {"data": "fresh"}
    finally:
        middleware_module.get_response_cache_middleware = original_getter


def test_api_list_cached_responses(client: TestClient, cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))

    response = client.get("/performance/cache/responses")

    assert response.status_code == 200
    body = response.json()
    assert len(body["responses"]) == 1
    assert body["size"] == 1


def test_api_clear_cached_responses(client: TestClient, cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))

    response = client.delete("/performance/cache/responses")
    assert response.status_code == 204

    assert cache.stats()["size"] == 0


def test_api_invalidate_by_key(client: TestClient, cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))
    key = cache.cache_key("GET", "/notebooks")

    response = client.post("/performance/cache/responses/invalidate", json={"key": key})

    assert response.status_code == 200
    assert response.json()["invalidated"] == 1
    assert cache.stats()["size"] == 0


def test_api_invalidate_by_path(client: TestClient, cache: ResponseCacheMiddleware):
    cache.after_response(CacheContext(method="GET", path="/notebooks", status_code=200, body={"ok": True}))

    response = client.post("/performance/cache/responses/invalidate", json={"path": "/notebooks"})

    assert response.status_code == 200
    assert response.json()["invalidated"] == 1
