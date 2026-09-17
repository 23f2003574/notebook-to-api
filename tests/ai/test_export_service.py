import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.dashboard import export_router
from backend.ai.export_service import (
    ExportFormat,
    ExportManifest,
    ModelExport,
    ModelExportService,
    get_model_export_service,
)
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
def service(
    registry: ModelRegistry,
    deployments: ModelDeploymentManager,
    benchmarks: ModelBenchmarkService,
    analytics: InferenceAnalyticsService,
) -> ModelExportService:
    return ModelExportService(registry=registry, deployments=deployments, benchmarks=benchmarks, analytics=analytics)


@pytest.fixture
def client(service: ModelExportService) -> TestClient:
    app = FastAPI()
    app.include_router(export_router)
    app.dependency_overrides[get_model_export_service] = lambda: service
    return TestClient(app)


def test_export_models_returns_json_by_default(registry: ModelRegistry, service: ModelExportService):
    registry.register("gpt-a", "1.0.0", ModelMetadata(provider="openai"))

    export = service.export_models()

    assert isinstance(export, ModelExport)
    assert export.section == "models"
    assert export.format == ExportFormat.JSON
    payload = json.loads(export.content)
    assert payload[0]["name"] == "gpt-a"


def test_export_models_csv_format(registry: ModelRegistry, service: ModelExportService):
    registry.register("gpt-a", "1.0.0")

    export = service.export_models(fmt=ExportFormat.CSV)

    assert export.content_type == "text/csv"
    assert "name" in export.content.splitlines()[0]


def test_export_models_yaml_format(registry: ModelRegistry, service: ModelExportService):
    registry.register("gpt-a", "1.0.0")

    export = service.export_models(fmt=ExportFormat.YAML)

    assert export.content_type == "application/x-yaml"
    assert "name: gpt-a" in export.content


def test_export_deployments_includes_registered_deployments(
    registry: ModelRegistry, deployments: ModelDeploymentManager, service: ModelExportService
):
    registry.register("gpt-a", "1.0.0")
    deployment = deployments.deploy("gpt-a", "1.0.0", registry=registry)

    export = service.export_deployments()

    payload = json.loads(export.content)
    assert payload[0]["deployment_id"] == deployment.deployment_id


def test_export_benchmarks_includes_benchmark_results(
    registry: ModelRegistry, benchmarks: ModelBenchmarkService, service: ModelExportService
):
    registry.register("gpt-a", "1.0.0")
    result = benchmarks.run("standard-suite", "gpt-a", registry=registry)

    export = service.export_benchmarks()

    payload = json.loads(export.content)
    assert payload[0]["benchmark_id"] == result.benchmark_id


def test_export_all_returns_manifest_with_checksum(
    registry: ModelRegistry,
    deployments: ModelDeploymentManager,
    benchmarks: ModelBenchmarkService,
    analytics: InferenceAnalyticsService,
    service: ModelExportService,
):
    registry.register("gpt-a", "1.0.0")
    deployments.deploy("gpt-a", "1.0.0", registry=registry)
    benchmarks.run("standard-suite", "gpt-a", registry=registry)
    analytics.record("gpt-a", "success", 100.0, 10)

    manifest = service.export_all()

    assert isinstance(manifest, ExportManifest)
    assert manifest.sections == ("models", "deployments", "benchmarks", "analytics")
    assert len(manifest.checksum) == 64
    payload = json.loads(manifest.export.content)
    assert len(payload["models"]) == 1
    assert len(payload["deployments"]) == 1
    assert len(payload["benchmarks"]) == 1
    assert payload["analytics"]["request_count"] == 1


def test_export_all_ids_increment(registry: ModelRegistry, service: ModelExportService):
    first = service.export_all()
    second = service.export_all()

    assert first.export_id != second.export_id


def test_api_export_models(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")

    response = client.get("/ai/export/models")

    assert response.status_code == 200
    assert response.json()["section"] == "models"


def test_api_export_models_invalid_format_returns_422(client: TestClient):
    response = client.get("/ai/export/models", params={"format": "xml"})

    assert response.status_code == 422


def test_api_export_deployments(
    client: TestClient, registry: ModelRegistry, deployments: ModelDeploymentManager
):
    registry.register("gpt-a", "1.0.0")
    deployments.deploy("gpt-a", "1.0.0", registry=registry)

    response = client.get("/ai/export/deployments")

    assert response.status_code == 200
    assert response.json()["section"] == "deployments"


def test_api_export_benchmarks(client: TestClient, registry: ModelRegistry, benchmarks: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0")
    benchmarks.run("standard-suite", "gpt-a", registry=registry)

    response = client.get("/ai/export/benchmarks")

    assert response.status_code == 200
    assert response.json()["section"] == "benchmarks"


def test_api_export_all(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")

    response = client.get("/ai/export/all", params={"format": "csv"})

    assert response.status_code == 200
    body = response.json()
    assert "checksum" in body
    assert body["export"]["format"] == "csv"
