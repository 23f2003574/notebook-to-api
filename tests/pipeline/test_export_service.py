import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.dashboard import export_router
from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType
from backend.pipeline.etl_engine import ETLWorkflowEngine
from backend.pipeline.export_service import (
    ExportFormat,
    ExportManifest,
    PipelineExport,
    PipelineExportService,
    get_pipeline_export_service,
)
from backend.pipeline.pipeline_analytics import PipelineAnalyticsService
from backend.pipeline.pipeline_executor import PipelineExecutionEngine
from backend.pipeline.pipeline_registry import PipelineMetadata, PipelineRegistry
from backend.pipeline.schema_registry import SchemaRegistry
from backend.pipeline.transformation_engine import DataTransformationEngine

ROWS = [{"region": "east", "amount": 10}, {"region": "west", "amount": 5}]


@pytest.fixture
def registry() -> PipelineRegistry:
    return PipelineRegistry()


@pytest.fixture
def sources() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    return manager


@pytest.fixture
def workflows(sources: DataSourceManager) -> ETLWorkflowEngine:
    engine = ETLWorkflowEngine()
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")
    return engine


@pytest.fixture
def executor() -> PipelineExecutionEngine:
    return PipelineExecutionEngine()


@pytest.fixture
def schemas() -> SchemaRegistry:
    return SchemaRegistry()


@pytest.fixture
def analytics() -> PipelineAnalyticsService:
    return PipelineAnalyticsService()


@pytest.fixture
def service(
    registry: PipelineRegistry, executor: PipelineExecutionEngine, schemas: SchemaRegistry, analytics: PipelineAnalyticsService
) -> PipelineExportService:
    return PipelineExportService(registry=registry, executor=executor, schemas=schemas, analytics=analytics)


@pytest.fixture
def client(service: PipelineExportService) -> TestClient:
    app = FastAPI()
    app.include_router(export_router)
    app.dependency_overrides[get_pipeline_export_service] = lambda: service
    return TestClient(app)


def test_export_pipeline_json_contains_registered_definitions(service: PipelineExportService, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0", PipelineMetadata(description="Ingests orders"))

    export = service.export_pipeline(fmt=ExportFormat.JSON)

    assert isinstance(export, PipelineExport)
    assert export.section == "definitions"
    assert export.content_type == "application/json"
    data = json.loads(export.content)
    assert data[0]["name"] == "ingest-orders"


def test_export_pipeline_filters_by_name(service: PipelineExportService, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-users", "1.0.0")

    export = service.export_pipeline(fmt=ExportFormat.JSON, names=["ingest-orders"])

    data = json.loads(export.content)
    assert [pipeline["name"] for pipeline in data] == ["ingest-orders"]


def test_export_pipeline_empty_by_default(service: PipelineExportService):
    export = service.export_pipeline(fmt=ExportFormat.JSON)

    assert json.loads(export.content) == []


def test_export_runs_json_contains_recorded_runs(
    service: PipelineExportService, executor: PipelineExecutionEngine, workflows, sources
):
    run = executor.submit("orders-etl", ROWS)
    executor.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=DataTransformationEngine())

    export = service.export_runs(fmt=ExportFormat.JSON)

    data = json.loads(export.content)
    assert len(data) == 1
    assert data[0]["run_id"] == run.run_id


def test_export_runs_filters_by_workflow_name(service: PipelineExportService, executor: PipelineExecutionEngine):
    executor.submit("orders-etl", ROWS)
    executor.submit("other-etl", ROWS)

    export = service.export_runs(fmt=ExportFormat.JSON, workflow_name="other-etl")

    data = json.loads(export.content)
    assert len(data) == 1
    assert data[0]["workflow_name"] == "other-etl"


def test_export_schemas_json_contains_version_history(service: PipelineExportService, schemas: SchemaRegistry):
    schemas.register("orders", [{"name": "region", "type": "str", "nullable": False}])
    schemas.update("orders", [{"name": "region", "type": "str", "nullable": False}, {"name": "currency", "type": "str", "nullable": True}])

    export = service.export_schemas(fmt=ExportFormat.JSON)

    data = json.loads(export.content)
    assert data[0]["name"] == "orders"
    assert len(data[0]["versions"]) == 2


def test_export_csv_format_produces_flat_rows(service: PipelineExportService, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")

    export = service.export_pipeline(fmt=ExportFormat.CSV)

    assert export.content_type == "text/csv"
    assert "name" in export.content.splitlines()[0]


def test_export_yaml_format_produces_yaml_text(service: PipelineExportService, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")

    export = service.export_pipeline(fmt=ExportFormat.YAML)

    assert export.content_type == "application/x-yaml"
    assert "name: ingest-orders" in export.content


def test_export_all_bundles_every_section_with_manifest(
    service: PipelineExportService,
    registry: PipelineRegistry,
    executor: PipelineExecutionEngine,
    schemas: SchemaRegistry,
    analytics: PipelineAnalyticsService,
    workflows,
    sources,
):
    registry.register("ingest-orders", "1.0.0")
    run = executor.submit("orders-etl", ROWS)
    executor.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=DataTransformationEngine())
    schemas.register("orders", [{"name": "region", "type": "str", "nullable": False}])
    analytics.record("orders-etl", "success", 100.0, 10)

    manifest = service.export_all(fmt=ExportFormat.JSON)

    assert isinstance(manifest, ExportManifest)
    assert manifest.sections == ("definitions", "runs", "schemas", "analytics")
    assert manifest.checksum
    data = json.loads(manifest.export.content)
    assert len(data["definitions"]) == 1
    assert len(data["runs"]) == 1
    assert len(data["schemas"]) == 1
    assert "execution_count" in data["analytics"]


def test_export_all_checksum_reflects_content(service: PipelineExportService):
    import hashlib

    manifest = service.export_all(fmt=ExportFormat.JSON)

    assert manifest.checksum == hashlib.sha256(manifest.export.content.encode("utf-8")).hexdigest()


def test_export_all_generates_incrementing_export_ids(service: PipelineExportService):
    first = service.export_all(fmt=ExportFormat.JSON)
    second = service.export_all(fmt=ExportFormat.JSON)

    assert first.export_id != second.export_id


# --- API tests -------------------------------------------------------------


def test_api_export_definitions(client: TestClient, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")

    response = client.get("/pipelines/export/definitions")

    assert response.status_code == 200
    data = json.loads(response.json()["content"])
    assert data[0]["name"] == "ingest-orders"


def test_api_export_definitions_filters_by_names(client: TestClient, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-users", "1.0.0")

    response = client.get("/pipelines/export/definitions", params={"names": "ingest-orders"})

    data = json.loads(response.json()["content"])
    assert [pipeline["name"] for pipeline in data] == ["ingest-orders"]


def test_api_export_runs(client: TestClient, executor: PipelineExecutionEngine):
    executor.submit("orders-etl", ROWS)

    response = client.get("/pipelines/export/runs")

    assert response.status_code == 200
    data = json.loads(response.json()["content"])
    assert len(data) == 1


def test_api_export_schemas(client: TestClient, schemas: SchemaRegistry):
    schemas.register("orders", [{"name": "region", "type": "str", "nullable": False}])

    response = client.get("/pipelines/export/schemas")

    assert response.status_code == 200
    data = json.loads(response.json()["content"])
    assert data[0]["name"] == "orders"


def test_api_export_all(client: TestClient, registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")

    response = client.get("/pipelines/export/all")

    assert response.status_code == 200
    body = response.json()
    assert "export_id" in body
    assert "checksum" in body


def test_api_export_invalid_format_returns_422(client: TestClient):
    response = client.get("/pipelines/export/definitions", params={"format": "xml"})

    assert response.status_code == 422
