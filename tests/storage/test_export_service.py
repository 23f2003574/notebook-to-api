import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactManager, ArtifactType
from backend.storage.dashboard import StorageDashboardAPI
from backend.storage.export_service import (
    StorageExport,
    StorageExportService,
    get_storage_export_service,
    router as export_service_router,
)
from backend.storage.object_storage import ObjectStorageEngine
from backend.storage.storage_analytics import StorageAnalyticsService
from backend.storage.storage_registry import StorageRegistry


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def artifact_manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def storage_registry() -> StorageRegistry:
    return StorageRegistry()


@pytest.fixture
def analytics_service(object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager) -> StorageAnalyticsService:
    return StorageAnalyticsService(object_storage=object_storage, artifact_manager=artifact_manager)


@pytest.fixture
def dashboard(
    artifact_manager: ArtifactManager, analytics_service: StorageAnalyticsService, storage_registry: StorageRegistry
) -> StorageDashboardAPI:
    return StorageDashboardAPI(
        artifact_manager=artifact_manager, analytics_service=analytics_service, storage_registry=storage_registry
    )


@pytest.fixture
def service(dashboard: StorageDashboardAPI) -> StorageExportService:
    return StorageExportService(dashboard)


@pytest.fixture
def client(service: StorageExportService) -> TestClient:
    app = FastAPI()
    app.include_router(export_service_router)
    app.dependency_overrides[get_storage_export_service] = lambda: service
    return TestClient(app)


def test_export_artifacts_json(artifact_manager: ArtifactManager, service: StorageExportService):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    export = service.export_artifacts(format="json")

    assert isinstance(export, StorageExport)
    assert export.manifest.export_type == "artifacts"
    assert export.manifest.record_count == 1
    parsed = json.loads(export.content)
    assert parsed[0]["name"] == "model.pkl"


def test_export_artifacts_csv_has_header_and_row(artifact_manager: ArtifactManager, service: StorageExportService):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    export = service.export_artifacts(format="csv")

    lines = export.content.splitlines()
    assert "artifact_id" in lines[0]
    assert "model.pkl" in lines[1]


def test_export_artifacts_yaml_contains_name(artifact_manager: ArtifactManager, service: StorageExportService):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    export = service.export_artifacts(format="yaml")

    assert "name: model.pkl" in export.content


def test_export_artifacts_rejects_unsupported_format(service: StorageExportService):
    with pytest.raises(ValueError):
        service.export_artifacts(format="xml")


def test_export_inventory_reports_usage(
    object_storage: ObjectStorageEngine, storage_registry: StorageRegistry, service: StorageExportService
):
    object_storage.put("a.txt", b"hello")

    export = service.export_inventory(format="json")

    assert export.manifest.export_type == "inventory"
    parsed = json.loads(export.content)
    assert parsed["storage_usage_bytes"] == len(b"hello")
    assert parsed["object_count"] == 1


def test_export_analytics_includes_summary(
    object_storage: ObjectStorageEngine, service: StorageExportService
):
    object_storage.put("a.txt", b"hello")

    export = service.export_analytics(format="json")

    assert export.manifest.export_type == "analytics"
    parsed = json.loads(export.content)
    assert "summary" in parsed


def test_export_all_bundles_every_section(
    artifact_manager: ArtifactManager, object_storage: ObjectStorageEngine, service: StorageExportService
):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    export = service.export_all(format="json")

    parsed = json.loads(export.content)
    assert set(parsed.keys()) == {"overview", "artifacts", "capacity", "analytics"}
    assert export.manifest.export_type == "all"


def test_manifest_checksum_reflects_content(artifact_manager: ArtifactManager, service: StorageExportService):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    export = service.export_artifacts(format="json")

    assert export.manifest.checksum == hashlib.sha256(export.content.encode("utf-8")).hexdigest()


def test_api_export_artifacts_json(client: TestClient, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    response = client.get("/storage/export/artifacts")

    assert response.status_code == 200
    assert response.json()["data"][0]["name"] == "model.pkl"


def test_api_export_artifacts_csv(client: TestClient, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    response = client.get("/storage/export/artifacts", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "model.pkl" in response.text


def test_api_export_artifacts_yaml(client: TestClient, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    response = client.get("/storage/export/artifacts", params={"format": "yaml"})

    assert response.status_code == 200
    assert "application/x-yaml" in response.headers["content-type"]
    assert "name: model.pkl" in response.text


def test_api_export_artifacts_invalid_format_returns_422(client: TestClient):
    response = client.get("/storage/export/artifacts", params={"format": "xml"})

    assert response.status_code == 422


def test_api_export_inventory(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.get("/storage/export/inventory")

    assert response.status_code == 200
    assert response.json()["data"]["object_count"] == 1


def test_api_export_analytics(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.get("/storage/export/analytics")

    assert response.status_code == 200
    assert "summary" in response.json()["data"]


def test_api_export_all(client: TestClient, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    response = client.get("/storage/export/all")

    assert response.status_code == 200
    assert "artifacts" in response.json()["data"]
