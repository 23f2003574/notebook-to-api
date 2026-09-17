import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.dashboard import ModelDashboardAPI, get_model_dashboard_api, router as dashboard_router
from backend.ai.inference_analytics import InferenceAnalyticsService
from backend.ai.model_benchmark import ModelBenchmarkService
from backend.ai.model_deployment import ModelDeploymentManager
from backend.ai.model_registry import ModelMetadata, ModelRegistry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def deployments() -> ModelDeploymentManager:
    return ModelDeploymentManager()


@pytest.fixture
def benchmarks() -> ModelBenchmarkService:
    return ModelBenchmarkService()


@pytest.fixture
def analytics() -> InferenceAnalyticsService:
    return InferenceAnalyticsService()


@pytest.fixture
def api(
    registry: ModelRegistry,
    deployments: ModelDeploymentManager,
    benchmarks: ModelBenchmarkService,
    analytics: InferenceAnalyticsService,
) -> ModelDashboardAPI:
    return ModelDashboardAPI(registry=registry, deployments=deployments, benchmarks=benchmarks, analytics=analytics)


@pytest.fixture
def client(api: ModelDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_model_dashboard_api] = lambda: api
    return TestClient(app)


def test_models_reports_registered_inventory(registry: ModelRegistry, api: ModelDashboardAPI):
    registry.register("gpt-a", "1.0.0")
    registry.register("gpt-b", "1.0.0")

    section = api.models()

    assert section["total_models"] == 2
    assert {model["name"] for model in section["models"]} == {"gpt-a", "gpt-b"}


def test_deployments_reports_counts_by_target(
    registry: ModelRegistry, deployments: ModelDeploymentManager, api: ModelDashboardAPI
):
    registry.register("gpt-a", "1.0.0")
    deployment = deployments.deploy("gpt-a", "1.0.0", registry=registry, target="staging")
    deployments.deploy("gpt-a", "1.0.0", registry=registry, target="development")

    section = api.deployments()

    assert section["total_deployments"] == 2
    assert section["by_target"] == {"staging": 1, "development": 1}
    assert any(item["deployment_id"] == deployment.deployment_id for item in section["recent_deployments"])


def test_analytics_reports_summary_and_recent_activity(
    analytics: InferenceAnalyticsService, api: ModelDashboardAPI
):
    analytics.record("gpt-a", "success", 100.0, 10)
    analytics.record("gpt-a", "failure", 200.0, 0)

    section = api.analytics()

    assert section["request_count"] == 2
    assert len(section["recent_activity"]) == 2


def test_overview_combines_all_sections(
    registry: ModelRegistry,
    deployments: ModelDeploymentManager,
    benchmarks: ModelBenchmarkService,
    analytics: InferenceAnalyticsService,
    api: ModelDashboardAPI,
):
    registry.register("gpt-a", "1.0.0")
    deployments.deploy("gpt-a", "1.0.0", registry=registry)
    benchmarks.run("standard-suite", "gpt-a", registry=registry)
    analytics.record("gpt-a", "success", 100.0, 10)

    overview = api.overview()

    assert overview["models"]["total_models"] == 1
    assert overview["deployments"]["total_deployments"] == 1
    assert overview["benchmarks"]["total_benchmarks"] == 1
    assert overview["analytics"]["request_count"] == 1
    assert "generated_at" in overview


def test_api_dashboard_overview(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")

    response = client.get("/ai/dashboard")

    assert response.status_code == 200
    assert response.json()["models"]["total_models"] == 1


def test_api_dashboard_models(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0", ModelMetadata(provider="openai"))

    response = client.get("/ai/dashboard/models")

    assert response.status_code == 200
    assert response.json()["total_models"] == 1


def test_api_dashboard_deployments(client: TestClient, registry: ModelRegistry, deployments: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    deployments.deploy("gpt-a", "1.0.0", registry=registry)

    response = client.get("/ai/dashboard/deployments")

    assert response.status_code == 200
    assert response.json()["total_deployments"] == 1


def test_api_dashboard_analytics(client: TestClient, analytics: InferenceAnalyticsService):
    analytics.record("gpt-a", "success", 100.0, 10)

    response = client.get("/ai/dashboard/analytics")

    assert response.status_code == 200
    assert response.json()["request_count"] == 1
