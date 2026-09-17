import pytest

from backend.gateway.api_gateway import APIGateway
from backend.gateway.api_versioning import APIVersionManager
from backend.gateway.bootstrap import (
    GatewayAlreadyInitializedError,
    GatewayBootstrap,
    GatewayNotInitializedError,
    get_bootstrap,
)
from backend.gateway.gateway_analytics import GatewayAnalytics
from backend.gateway.gateway_dashboard_api import GatewayDashboardAPI, GatewayHealth
from backend.gateway.gateway_export import GatewayExportService
from backend.gateway.load_balancer import LoadBalancer
from backend.gateway.middleware import MiddlewarePipeline
from backend.gateway.rate_limiter import RateLimiter
from backend.gateway.request_router import RequestRouter
from backend.gateway.request_validation import RequestValidationEngine
from backend.gateway.route_registry import RouteRegistry
from backend.gateway.traffic_policy import TrafficPolicyEngine


@pytest.fixture
def bootstrap() -> GatewayBootstrap:
    return GatewayBootstrap()


# --- initialize ---


def test_initialize_creates_every_component(bootstrap: GatewayBootstrap):
    result = bootstrap.initialize()

    assert result is bootstrap
    assert bootstrap.is_initialized is True
    assert isinstance(bootstrap.gateway, APIGateway)
    assert isinstance(bootstrap.route_registry, RouteRegistry)
    assert isinstance(bootstrap.analytics, GatewayAnalytics)
    assert isinstance(bootstrap.middleware_pipeline, MiddlewarePipeline)
    assert isinstance(bootstrap.rate_limiter, RateLimiter)
    assert isinstance(bootstrap.validation_engine, RequestValidationEngine)
    assert isinstance(bootstrap.load_balancer, LoadBalancer)
    assert isinstance(bootstrap.policy_engine, TrafficPolicyEngine)
    assert isinstance(bootstrap.version_manager, APIVersionManager)
    assert isinstance(bootstrap.request_router, RequestRouter)
    assert isinstance(bootstrap.dashboard, GatewayDashboardAPI)
    assert isinstance(bootstrap.export_service, GatewayExportService)


def test_initialize_starts_the_gateway(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    assert bootstrap.gateway.status()["status"] == "running"


def test_initialize_twice_raises(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    with pytest.raises(GatewayAlreadyInitializedError):
        bootstrap.initialize()


def test_analytics_is_shared_with_gateway(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    assert bootstrap.analytics is bootstrap.gateway.analytics


def test_route_registry_is_shared_with_gateway(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    assert bootstrap.route_registry is bootstrap.gateway.route_registry


# --- register_components ---


def test_register_components_adds_default_middleware(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    names = {middleware.name for middleware in bootstrap.middleware_pipeline.list_middleware()}

    assert "logging" in names
    assert "cors" in names


def test_register_components_is_idempotent(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    bootstrap.register_components()

    names = [middleware.name for middleware in bootstrap.middleware_pipeline.list_middleware()]
    assert names.count("logging") == 1
    assert names.count("cors") == 1


# --- wire_pipeline ---


def test_request_router_resolves_registered_routes(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.route_registry.register("/notebooks", ["GET"])
    bootstrap.load_balancer.register_backend("api-1", "http://api-1")

    result = bootstrap.request_router.route("/notebooks", "GET")

    assert result.matched is True
    assert result.route.path == "/notebooks"
    assert result.backend["name"] == "api-1"


def test_request_router_reports_no_healthy_backend_when_none_registered(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.route_registry.register("/notebooks", ["GET"])

    result = bootstrap.request_router.route("/notebooks", "GET")

    assert result.matched is False
    assert result.reason == "no_healthy_backend"


def test_dashboard_overview_reflects_registered_route(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.route_registry.register("/notebooks", ["GET"])

    overview = bootstrap.dashboard.overview()

    assert overview.total_routes == 1
    assert overview.gateway_status == "running"


def test_dashboard_reflects_dispatch_metrics(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.gateway.register_route("echo", lambda payload: payload)

    bootstrap.gateway.dispatch("echo", {})

    assert bootstrap.dashboard.metrics()["total_responses"] == 1


def test_export_service_exports_registered_routes(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.route_registry.register("/notebooks", ["GET"])

    export = bootstrap.export_service.export_routes("json")

    assert export.data[0]["path"] == "/notebooks"


def test_middleware_pipeline_runs_during_routing(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.route_registry.register("/notebooks", ["GET"])
    bootstrap.load_balancer.register_backend("api-1", "http://api-1")

    calls = []
    bootstrap.middleware_pipeline.register("tracker", before=lambda ctx: calls.append(ctx.path))

    bootstrap.request_router.route("/notebooks", "GET")

    assert calls == ["/notebooks"]


# --- health_check ---


def test_health_check_reports_healthy_after_initialize(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    health = bootstrap.health_check()

    assert isinstance(health, GatewayHealth)
    assert health.status == "healthy"


def test_health_check_without_initialize_raises(bootstrap: GatewayBootstrap):
    with pytest.raises(GatewayNotInitializedError):
        bootstrap.health_check()


# --- shutdown ---


def test_shutdown_stops_gateway_and_clears_initialized_flag(bootstrap: GatewayBootstrap):
    bootstrap.initialize()

    bootstrap.shutdown()

    assert bootstrap.is_initialized is False
    assert bootstrap.gateway.status()["status"] == "stopped"


def test_shutdown_without_initialize_raises(bootstrap: GatewayBootstrap):
    with pytest.raises(GatewayNotInitializedError):
        bootstrap.shutdown()


def test_reinitialize_after_shutdown_creates_fresh_components(bootstrap: GatewayBootstrap):
    bootstrap.initialize()
    bootstrap.route_registry.register("/notebooks", ["GET"])
    bootstrap.shutdown()

    bootstrap.initialize()

    assert bootstrap.route_registry.list_routes() == []
    assert bootstrap.gateway.status()["status"] == "running"


# --- module-level singleton ---


def test_get_bootstrap_returns_same_instance():
    assert get_bootstrap() is get_bootstrap()


def test_default_bootstrap_is_not_initialized_on_import():
    assert get_bootstrap().is_initialized is False
