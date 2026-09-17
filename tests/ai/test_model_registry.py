import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.model_registry import (
    ModelAlreadyRegisteredError,
    ModelInfo,
    ModelMetadata,
    ModelRegistry,
    UnknownModelError,
    get_model_registry,
    router as model_registry_router,
)


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def client(registry: ModelRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(model_registry_router)
    app.dependency_overrides[get_model_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_model(registry: ModelRegistry):
    model = registry.register(
        "gpt-embed", "1.0.0", ModelMetadata(description="Embedding model", provider="openai")
    )

    assert isinstance(model, ModelInfo)
    assert model.name == "gpt-embed"
    assert model.version == "1.0.0"
    assert model.metadata.provider == "openai"


def test_metadata_source_defaults_to_empty_string():
    assert ModelMetadata().source == ""


def test_metadata_source_round_trips_through_dict():
    metadata = ModelMetadata.from_dict({"source": "catalog:official"})

    assert metadata.source == "catalog:official"
    assert metadata.to_dict()["source"] == "catalog:official"


def test_register_rejects_empty_name(registry: ModelRegistry):
    with pytest.raises(ValueError):
        registry.register("", "1.0.0")


def test_register_rejects_empty_version(registry: ModelRegistry):
    with pytest.raises(ValueError):
        registry.register("gpt-embed", "")


def test_register_rejects_duplicate_version(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")

    with pytest.raises(ModelAlreadyRegisteredError):
        registry.register("gpt-embed", "1.0.0")


def test_register_allows_new_version(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")
    second = registry.register("gpt-embed", "2.0.0")

    assert second.version == "2.0.0"


def test_get_returns_latest_version_by_default(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")
    registry.register("gpt-embed", "2.0.0")

    assert registry.get("gpt-embed").version == "2.0.0"


def test_get_returns_specific_version(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")
    registry.register("gpt-embed", "2.0.0")

    assert registry.get("gpt-embed", version="1.0.0").version == "1.0.0"


def test_get_unknown_model_raises(registry: ModelRegistry):
    with pytest.raises(UnknownModelError):
        registry.get("does-not-exist")


def test_get_unknown_version_raises(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")

    with pytest.raises(UnknownModelError):
        registry.get("gpt-embed", version="9.9.9")


def test_is_registered_checks_name_and_version(registry: ModelRegistry):
    assert registry.is_registered("gpt-embed") is False

    registry.register("gpt-embed", "1.0.0")

    assert registry.is_registered("gpt-embed") is True
    assert registry.is_registered("gpt-embed", version="1.0.0") is True
    assert registry.is_registered("gpt-embed", version="9.9.9") is False


def test_list_models_returns_latest_of_each(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")
    registry.register("gpt-embed", "2.0.0")
    registry.register("gpt-chat", "1.0.0")

    listed = {model.name: model.version for model in registry.list_models()}

    assert listed == {"gpt-embed": "2.0.0", "gpt-chat": "1.0.0"}


def test_list_models_filters_by_capability(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0", ModelMetadata(capabilities=("embeddings",)))
    registry.register("gpt-chat", "1.0.0", ModelMetadata(capabilities=("chat",)))

    listed = registry.list_models(capability="embeddings")

    assert [model.name for model in listed] == ["gpt-embed"]


def test_remove_removes_all_versions(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")
    registry.register("gpt-embed", "2.0.0")

    registry.remove("gpt-embed")

    with pytest.raises(UnknownModelError):
        registry.get("gpt-embed")


def test_remove_removes_single_version(registry: ModelRegistry):
    registry.register("gpt-embed", "1.0.0")
    registry.register("gpt-embed", "2.0.0")

    registry.remove("gpt-embed", version="1.0.0")

    assert registry.get("gpt-embed").version == "2.0.0"


def test_remove_unknown_model_raises(registry: ModelRegistry):
    with pytest.raises(UnknownModelError):
        registry.remove("does-not-exist")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/ai/models",
        json={"name": "gpt-embed", "version": "1.0.0", "metadata": {"provider": "openai"}},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "gpt-embed"

    listed = client.get("/ai/models")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/ai/models", json={"name": "gpt-embed", "version": "1.0.0"})
    response = client.post("/ai/models", json={"name": "gpt-embed", "version": "1.0.0"})

    assert response.status_code == 409


def test_api_get_unknown_model_returns_404(client: TestClient):
    response = client.get("/ai/models/does-not-exist")

    assert response.status_code == 404


def test_api_delete_removes_model(client: TestClient):
    client.post("/ai/models", json={"name": "gpt-embed", "version": "1.0.0"})

    response = client.delete("/ai/models/gpt-embed")
    assert response.status_code == 204

    assert client.get("/ai/models/gpt-embed").status_code == 404
