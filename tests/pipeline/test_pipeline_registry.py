import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.pipeline_registry import (
    Pipeline,
    PipelineAlreadyRegisteredError,
    PipelineMetadata,
    PipelineRegistry,
    UnknownPipelineError,
    get_pipeline_registry,
    router as pipeline_registry_router,
)


@pytest.fixture
def registry() -> PipelineRegistry:
    return PipelineRegistry()


@pytest.fixture
def client(registry: PipelineRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(pipeline_registry_router)
    app.dependency_overrides[get_pipeline_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_pipeline(registry: PipelineRegistry):
    pipeline = registry.register(
        "ingest-orders", "1.0.0", PipelineMetadata(description="Ingests orders", owner="alice")
    )

    assert isinstance(pipeline, Pipeline)
    assert pipeline.name == "ingest-orders"
    assert pipeline.version == "1.0.0"
    assert pipeline.metadata.owner == "alice"


def test_metadata_source_defaults_to_empty_string():
    assert PipelineMetadata().source == ""


def test_metadata_source_round_trips_through_dict():
    metadata = PipelineMetadata.from_dict({"source": "catalog:official"})

    assert metadata.source == "catalog:official"
    assert metadata.to_dict()["source"] == "catalog:official"


def test_register_rejects_empty_name(registry: PipelineRegistry):
    with pytest.raises(ValueError):
        registry.register("", "1.0.0")


def test_register_rejects_empty_version(registry: PipelineRegistry):
    with pytest.raises(ValueError):
        registry.register("ingest-orders", "")


def test_register_rejects_duplicate_version(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")

    with pytest.raises(PipelineAlreadyRegisteredError):
        registry.register("ingest-orders", "1.0.0")


def test_register_allows_new_version(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    second = registry.register("ingest-orders", "2.0.0")

    assert second.version == "2.0.0"


def test_get_returns_latest_version_by_default(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-orders", "2.0.0")

    assert registry.get("ingest-orders").version == "2.0.0"


def test_get_returns_specific_version(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-orders", "2.0.0")

    assert registry.get("ingest-orders", version="1.0.0").version == "1.0.0"


def test_get_unknown_pipeline_raises(registry: PipelineRegistry):
    with pytest.raises(UnknownPipelineError):
        registry.get("does-not-exist")


def test_get_unknown_version_raises(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")

    with pytest.raises(UnknownPipelineError):
        registry.get("ingest-orders", version="9.9.9")


def test_is_registered_checks_name_and_version(registry: PipelineRegistry):
    assert registry.is_registered("ingest-orders") is False

    registry.register("ingest-orders", "1.0.0")

    assert registry.is_registered("ingest-orders") is True
    assert registry.is_registered("ingest-orders", version="1.0.0") is True
    assert registry.is_registered("ingest-orders", version="9.9.9") is False


def test_list_pipelines_returns_latest_of_each(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-orders", "2.0.0")
    registry.register("ingest-users", "1.0.0")

    listed = {pipeline.name: pipeline.version for pipeline in registry.list_pipelines()}

    assert listed == {"ingest-orders": "2.0.0", "ingest-users": "1.0.0"}


def test_list_pipelines_filters_by_tag(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0", PipelineMetadata(tags=("etl",)))
    registry.register("auth-check", "1.0.0", PipelineMetadata(tags=("security",)))

    listed = registry.list_pipelines(tag="etl")

    assert [pipeline.name for pipeline in listed] == ["ingest-orders"]


def test_remove_removes_all_versions(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-orders", "2.0.0")

    registry.remove("ingest-orders")

    with pytest.raises(UnknownPipelineError):
        registry.get("ingest-orders")


def test_remove_removes_single_version(registry: PipelineRegistry):
    registry.register("ingest-orders", "1.0.0")
    registry.register("ingest-orders", "2.0.0")

    registry.remove("ingest-orders", version="1.0.0")

    assert registry.get("ingest-orders").version == "2.0.0"


def test_remove_unknown_pipeline_raises(registry: PipelineRegistry):
    with pytest.raises(UnknownPipelineError):
        registry.remove("does-not-exist")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/pipelines",
        json={"name": "ingest-orders", "version": "1.0.0", "metadata": {"owner": "alice"}},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "ingest-orders"

    listed = client.get("/pipelines")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/pipelines", json={"name": "ingest-orders", "version": "1.0.0"})
    response = client.post("/pipelines", json={"name": "ingest-orders", "version": "1.0.0"})

    assert response.status_code == 409


def test_api_get_unknown_pipeline_returns_404(client: TestClient):
    response = client.get("/pipelines/does-not-exist")

    assert response.status_code == 404


def test_api_delete_removes_pipeline(client: TestClient):
    client.post("/pipelines", json={"name": "ingest-orders", "version": "1.0.0"})

    response = client.delete("/pipelines/ingest-orders")
    assert response.status_code == 204

    assert client.get("/pipelines/ingest-orders").status_code == 404
