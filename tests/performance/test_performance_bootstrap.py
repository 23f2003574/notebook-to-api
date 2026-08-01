import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    PerformanceBootstrap,
    PerformanceBootstrapError,
    PerformanceNotInitializedError,
    UnknownServiceError,
    bootstrap_performance_subsystem,
    get_performance_bootstrap,
)
from backend.performance.compression import CompressionAlgorithm
from backend.gateway.middleware import get_middleware_pipeline


def test_register_services_wires_every_required_service():
    bootstrap = PerformanceBootstrap()

    services = bootstrap.register_services()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = PerformanceBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register_services()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = PerformanceBootstrap()
    bootstrap.register_services()

    profiler = bootstrap.discover("profiler")

    assert profiler is bootstrap.registered_services()["profiler"]


def test_discover_unknown_service_raises():
    bootstrap = PerformanceBootstrap()
    bootstrap.register_services()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_wire_components_registers_middleware_into_shared_pipeline():
    bootstrap = PerformanceBootstrap()

    wired = bootstrap.wire_components()

    assert set(wired) == {"response_caching", "compression_engine"}
    registered_names = {m.name for m in get_middleware_pipeline().list_middleware()}
    assert "response_caching" in registered_names
    assert "compression_engine" in registered_names


def test_wire_components_is_idempotent():
    bootstrap = PerformanceBootstrap()

    first = bootstrap.wire_components()
    second = bootstrap.wire_components()

    assert first == second


def test_initialize_returns_valid_result():
    bootstrap = PerformanceBootstrap()

    result = bootstrap.initialize()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()
    assert set(result.wired_middleware) == {"response_caching", "compression_engine"}
    assert bootstrap.is_initialized is True


def test_initialize_raises_when_a_required_service_is_missing(monkeypatch):
    bootstrap = PerformanceBootstrap()
    incomplete = {name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"}
    monkeypatch.setattr(bootstrap, "register_services", lambda: incomplete)
    monkeypatch.setattr(bootstrap, "wire_components", lambda: ())

    with pytest.raises(PerformanceBootstrapError) as exc_info:
        bootstrap.initialize()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False
    assert bootstrap.is_initialized is False


def test_health_check_before_initialize_raises():
    bootstrap = PerformanceBootstrap()

    with pytest.raises(PerformanceNotInitializedError):
        bootstrap.health_check()


def test_health_check_delegates_to_the_dashboard():
    bootstrap = PerformanceBootstrap()
    bootstrap.initialize()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "cache" in report
    assert "resources" in report


def test_shutdown_before_initialize_raises():
    bootstrap = PerformanceBootstrap()

    with pytest.raises(PerformanceNotInitializedError):
        bootstrap.shutdown()


def test_shutdown_clears_caches_removes_middleware_and_resets_state():
    bootstrap = PerformanceBootstrap()
    bootstrap.initialize()
    services = bootstrap.registered_services()
    services["cache_manager"].put("shutdown-test-key", "value")
    services["memory_cache"].set("shutdown-test-key", "value")

    bootstrap.shutdown()

    assert bootstrap.is_initialized is False
    assert services["cache_manager"].stats().size == 0
    assert services["memory_cache"].stats().entries == 0
    registered_names = {m.name for m in get_middleware_pipeline().list_middleware()}
    assert "response_caching" not in registered_names
    assert "compression_engine" not in registered_names


def test_bootstrap_performance_subsystem_is_valid():
    result = bootstrap_performance_subsystem()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_performance_subsystem_is_idempotent():
    first = bootstrap_performance_subsystem()
    second = bootstrap_performance_subsystem()

    assert first.valid is True
    assert second.valid is True


def test_get_performance_bootstrap_returns_singleton():
    assert get_performance_bootstrap() is get_performance_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "caching_and_performance_optimization"


def test_connection_pool_manager_is_intentionally_absent():
    assert "connection_pool_manager" not in REQUIRED_SERVICES


def test_end_to_end_cache_workflow():
    result = bootstrap_performance_subsystem()
    assert result.valid is True

    services = get_performance_bootstrap().registered_services()
    cache_manager = services["cache_manager"]
    memory_cache = services["memory_cache"]
    eviction_engine = services["eviction_engine"]
    profiler = services["profiler"]
    pool_manager = services["resource_pool_manager"]
    compression_engine = services["compression_engine"]
    dashboard_api = services["dashboard_api"]
    export_service = services["export_service"]

    key = f"e2e-{uuid.uuid4().hex}"
    cache_manager.put(key, "hello", ttl_seconds=60)
    assert cache_manager.get(key).value == "hello"

    memory_cache.set(key, "hello")
    assert memory_cache.get(key).value == "hello"

    from backend.performance.cache_eviction import EvictionPolicy

    eviction_engine.configure(policy=EvictionPolicy.LRU)
    eviction_result = eviction_engine.evict(memory_cache, count=0)
    assert eviction_result.reason == "manual"

    session_id = f"e2e-session-{uuid.uuid4().hex}"
    profiler.start("bootstrap-e2e", session_id=session_id)
    profiler.stop(session_id)
    report = profiler.report(session_id)
    assert report.status == "stopped"

    from backend.performance.resource_pool import PoolType

    pool_name = f"e2e-pool-{uuid.uuid4().hex}"
    pool_manager.create_pool(pool_name, pool_type=PoolType.WORKER, max_size=1)
    resource = pool_manager.acquire(pool_name)
    pool_manager.release(pool_name, resource.resource_id)

    compressed = compression_engine.compress("hello world " * 20, algorithm=CompressionAlgorithm.GZIP)
    restored = compression_engine.decompress(compressed.data, algorithm=CompressionAlgorithm.GZIP)
    assert restored.decode("utf-8") == "hello world " * 20

    overview = dashboard_api.overview()
    assert overview["resources"]["pools"]

    manifest = export_service.export_all()
    assert manifest.sections == ("metrics", "cache", "profiles")

    cache_manager.delete(key)
    memory_cache.remove(key)


@pytest.fixture
def client() -> TestClient:
    from backend.performance.cache_manager import (
        router as cache_router,
        profile_router,
    )
    from backend.performance.profiler import pool_router, dashboard_router
    from backend.performance.dashboard import export_router

    app = FastAPI()
    for router in (cache_router, profile_router, pool_router, dashboard_router, export_router):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_performance_subsystem()

    assert client.get("/performance/dashboard").status_code == 200
    assert client.get("/performance/pools").status_code == 200
    assert client.get("/performance/export/metrics").status_code == 200
