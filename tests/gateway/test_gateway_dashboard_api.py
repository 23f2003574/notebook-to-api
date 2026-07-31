import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.api_gateway import APIGateway
from backend.gateway.api_versioning import APIVersionManager
from backend.gateway.gateway_dashboard_api import (
    DashboardOverview,
    GatewayDashboardAPI,
    GatewayHealth,
    get_dashboard_api,
    router as dashboard_router,
)
from backend.gateway.load_balancer import LoadBalancer
from backend.gateway.middleware import MiddlewarePipeline
from backend.gateway.route_registry import RouteMetadata, RouteRegistry
from backend.gateway.traffic_policy import PolicyResult, TrafficPolicyEngine


@pytest.fixture
def gateway() -> APIGateway:
    return APIGateway()


@pytest.fixture
def route_registry() -> RouteRegistry:
    return RouteRegistry()


@pytest.fixture
def load_balancer() -> LoadBalancer:
    return LoadBalancer()


@pytest.fixture
def middleware_pipeline() -> MiddlewarePipeline:
    return MiddlewarePipeline()


@pytest.fixture
def policy_engine() -> TrafficPolicyEngine:
    return TrafficPolicyEngine()


@pytest.fixture
def version_manager() -> APIVersionManager:
    return APIVersionManager()


@pytest.fixture
def dashboard(
    gateway: APIGateway,
    route_registry: RouteRegistry,
    load_balancer: LoadBalancer,
    middleware_pipeline: MiddlewarePipeline,
    policy_engine: TrafficPolicyEngine,
    version_manager: APIVersionManager,
) -> GatewayDashboardAPI:
    return GatewayDashboardAPI(
        gateway,
        gateway.analytics,
        route_registry,
        load_balancer=load_balancer,
        middleware_pipeline=middleware_pipeline,
        policy_engine=policy_engine,
        version_manager=version_manager,
    )


@pytest.fixture
def client(dashboard: GatewayDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_dashboard_api] = lambda: dashboard
    return TestClient(app)


# --- overview ---


def test_overview_reflects_stopped_gateway_with_no_resources(dashboard: GatewayDashboardAPI):
    overview = dashboard.overview()

    assert isinstance(overview, DashboardOverview)
    assert overview.gateway_status == "stopped"
    assert overview.dispatch_count == 0
    assert overview.total_routes == 0
    assert overview.total_backends == 0


def test_overview_counts_registered_resources(
    dashboard: GatewayDashboardAPI,
    route_registry: RouteRegistry,
    load_balancer: LoadBalancer,
    middleware_pipeline: MiddlewarePipeline,
    policy_engine: TrafficPolicyEngine,
    version_manager: APIVersionManager,
):
    route_registry.register("/notebooks", ["GET"], RouteMetadata())
    load_balancer.register_backend("api-1", "http://api-1")
    middleware_pipeline.register("logging", before=lambda ctx: None)
    policy_engine.register_policy("maint", lambda ctx: PolicyResult(policy="maint", matched=False, action="skip"))
    version_manager.register_version("v1")

    overview = dashboard.overview()

    assert overview.total_routes == 1
    assert overview.total_backends == 1
    assert overview.healthy_backends == 1
    assert overview.total_middleware == 1
    assert overview.total_policies == 1
    assert overview.total_versions == 1


def test_overview_reflects_dispatch_count(dashboard: GatewayDashboardAPI, gateway: APIGateway):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    overview = dashboard.overview()

    assert overview.gateway_status == "running"
    assert overview.dispatch_count == 1
    assert overview.analytics["total_responses"] == 1


# --- metrics ---


def test_metrics_reflects_dispatch_activity(dashboard: GatewayDashboardAPI, gateway: APIGateway):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})
    gateway.dispatch("echo", {})

    metrics = dashboard.metrics()

    assert metrics["total_responses"] == 2


# --- health ---


def test_health_unhealthy_when_gateway_not_running(dashboard: GatewayDashboardAPI):
    health = dashboard.health()

    assert isinstance(health, GatewayHealth)
    assert health.status == "unhealthy"
    assert health.gateway_running is False
    assert "gateway is not running" in health.issues


def test_health_healthy_when_running_with_no_backends_configured(
    dashboard: GatewayDashboardAPI, gateway: APIGateway
):
    gateway.start()

    health = dashboard.health()

    assert health.status == "healthy"
    assert health.gateway_running is True


def test_health_unhealthy_when_all_backends_down(
    dashboard: GatewayDashboardAPI, gateway: APIGateway, load_balancer: LoadBalancer
):
    gateway.start()
    load_balancer.register_backend("api-1", "http://api-1")
    load_balancer.mark_unhealthy("api-1", healthy=False)

    health = dashboard.health()

    assert health.status == "unhealthy"
    assert "no healthy backends available" in health.issues


def test_health_degraded_on_elevated_error_rate(dashboard: GatewayDashboardAPI, gateway: APIGateway):
    def failing_handler(payload):
        raise RuntimeError("boom")

    gateway.register_route("fail", failing_handler)
    gateway.start()
    with pytest.raises(RuntimeError):
        gateway.dispatch("fail", {})

    health = dashboard.health()

    assert health.status == "degraded"
    assert "elevated error rate" in health.issues


# --- configuration ---


def test_configuration_reports_registered_resources(
    dashboard: GatewayDashboardAPI,
    middleware_pipeline: MiddlewarePipeline,
    policy_engine: TrafficPolicyEngine,
    version_manager: APIVersionManager,
):
    middleware_pipeline.register("logging", before=lambda ctx: None)
    policy_engine.register_policy("maint", lambda ctx: PolicyResult(policy="maint", matched=False, action="skip"))
    version_manager.register_version("v1")

    configuration = dashboard.configuration()

    assert len(configuration["middleware"]) == 1
    assert len(configuration["policies"]) == 1
    assert len(configuration["versions"]) == 1
    assert configuration["load_balancer_strategy"] == "round_robin"


# --- reset_metrics ---


def test_reset_metrics_clears_analytics(dashboard: GatewayDashboardAPI, gateway: APIGateway):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    dashboard.reset_metrics()

    assert dashboard.metrics()["total_responses"] == 0


# --- API ---


def test_api_overview_endpoint(client: TestClient):
    response = client.get("/gateway/dashboard/overview")

    assert response.status_code == 200
    assert response.json()["gateway_status"] == "stopped"


def test_api_metrics_endpoint(client: TestClient, gateway: APIGateway):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    response = client.get("/gateway/dashboard/metrics")

    assert response.status_code == 200
    assert response.json()["total_responses"] == 1


def test_api_health_endpoint(client: TestClient):
    response = client.get("/gateway/dashboard/health")

    assert response.status_code == 200
    assert response.json()["status"] == "unhealthy"


def test_api_configuration_endpoint(client: TestClient, middleware_pipeline: MiddlewarePipeline):
    middleware_pipeline.register("logging", before=lambda ctx: None)

    response = client.get("/gateway/dashboard/configuration")

    assert response.status_code == 200
    assert len(response.json()["middleware"]) == 1


def test_api_reset_metrics_endpoint(client: TestClient, gateway: APIGateway):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    response = client.post("/gateway/dashboard/reset-metrics")

    assert response.status_code == 200
    assert client.get("/gateway/dashboard/metrics").json()["total_responses"] == 0
