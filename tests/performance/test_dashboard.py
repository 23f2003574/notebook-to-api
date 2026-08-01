import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.profiler import dashboard_router
from backend.performance.dashboard import PerformanceDashboardAPI, get_performance_dashboard_api
from backend.performance.cache_manager import CacheManager
from backend.performance.in_memory_cache import InMemoryCache
from backend.performance.cache_eviction import CacheEvictionEngine
from backend.performance.response_cache import ResponseCacheMiddleware, CacheContext
from backend.performance.distributed_cache import (
    CacheBackend,
    ConnectionConfig,
    DistributedCacheAdapter,
)
from backend.performance.resource_pool import PoolType, ResourcePoolManager
from backend.performance.profiler import PerformanceProfiler
from backend.performance.compression import CompressionAlgorithm, CompressionEngine


@pytest.fixture
def cache_manager() -> CacheManager:
    return CacheManager()


@pytest.fixture
def memory_cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def eviction_engine() -> CacheEvictionEngine:
    return CacheEvictionEngine()


@pytest.fixture
def response_cache() -> ResponseCacheMiddleware:
    return ResponseCacheMiddleware()


@pytest.fixture
def distributed_adapter() -> DistributedCacheAdapter:
    adapter = DistributedCacheAdapter()
    adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))
    return adapter


@pytest.fixture
def pool_manager() -> ResourcePoolManager:
    manager = ResourcePoolManager()
    manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=4)
    return manager


@pytest.fixture
def profiler() -> PerformanceProfiler:
    return PerformanceProfiler()


@pytest.fixture
def compression_engine() -> CompressionEngine:
    return CompressionEngine()


@pytest.fixture
def dashboard(
    cache_manager: CacheManager,
    memory_cache: InMemoryCache,
    eviction_engine: CacheEvictionEngine,
    response_cache: ResponseCacheMiddleware,
    distributed_adapter: DistributedCacheAdapter,
    pool_manager: ResourcePoolManager,
    profiler: PerformanceProfiler,
    compression_engine: CompressionEngine,
) -> PerformanceDashboardAPI:
    return PerformanceDashboardAPI(
        cache_manager=cache_manager,
        memory_cache=memory_cache,
        eviction_engine=eviction_engine,
        response_cache=response_cache,
        distributed_adapter=distributed_adapter,
        pool_manager=pool_manager,
        profiler=profiler,
        compression_engine=compression_engine,
    )


@pytest.fixture
def client(dashboard: PerformanceDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_performance_dashboard_api] = lambda: dashboard
    return TestClient(app)


def test_cache_section_aggregates_all_cache_subsystems(
    dashboard: PerformanceDashboardAPI, cache_manager: CacheManager, memory_cache: InMemoryCache
):
    cache_manager.put("a", 1)
    memory_cache.set("b", 2)

    section = dashboard.cache()

    assert section["cache_manager"]["size"] == 1
    assert section["memory_cache"]["entries"] == 1
    assert "eviction" in section
    assert "responses" in section


def test_resources_section_reports_pool_stats(dashboard: PerformanceDashboardAPI, pool_manager: ResourcePoolManager):
    pool_manager.acquire("workers")

    section = dashboard.resources()

    assert len(section["pools"]) == 1
    assert section["pools"][0]["name"] == "workers"
    assert section["pools"][0]["in_use"] == 1


def test_connections_section_reports_backend_health(
    dashboard: PerformanceDashboardAPI, distributed_adapter: DistributedCacheAdapter
):
    section = dashboard.connections()

    assert section["backends"][0]["name"] == "primary"
    assert section["health"][0]["healthy"] is True


def test_overview_bundles_every_section(
    dashboard: PerformanceDashboardAPI,
    cache_manager: CacheManager,
    profiler: PerformanceProfiler,
    compression_engine: CompressionEngine,
):
    cache_manager.put("a", 1)
    profiler.start("parse_notebook", session_id="s1")
    compression_engine.compress("hello world " * 20, algorithm=CompressionAlgorithm.GZIP)

    overview = dashboard.overview()

    assert overview["cache"]["cache_manager"]["size"] == 1
    assert "resources" in overview
    assert "connections" in overview
    assert overview["profiler"]["total_sessions"] == 1
    assert overview["profiler"]["running"] == 1
    assert overview["compression"]["by_algorithm"]["gzip"]["count"] == 1


def test_overview_with_no_activity_has_empty_sections(dashboard: PerformanceDashboardAPI):
    overview = dashboard.overview()

    assert overview["cache"]["cache_manager"]["size"] == 0
    assert overview["resources"]["pools"][0]["in_use"] == 0
    assert overview["profiler"]["total_sessions"] == 0


def test_api_dashboard_overview(client: TestClient, cache_manager: CacheManager):
    cache_manager.put("a", 1)

    response = client.get("/performance/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["cache"]["cache_manager"]["size"] == 1


def test_api_dashboard_cache(client: TestClient, memory_cache: InMemoryCache):
    memory_cache.set("a", 1)

    response = client.get("/performance/dashboard/cache")

    assert response.status_code == 200
    assert response.json()["memory_cache"]["entries"] == 1


def test_api_dashboard_resources(client: TestClient, pool_manager: ResourcePoolManager):
    pool_manager.acquire("workers")

    response = client.get("/performance/dashboard/resources")

    assert response.status_code == 200
    assert response.json()["pools"][0]["in_use"] == 1


def test_api_dashboard_connections(client: TestClient):
    response = client.get("/performance/dashboard/connections")

    assert response.status_code == 200
    body = response.json()
    assert body["backends"][0]["name"] == "primary"
