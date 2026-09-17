import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.model_loader import (
    LoadedModel,
    ModelLoader,
    ModelManifest,
    ModelNotLoadedError,
    ModelValidationError,
    get_model_loader,
    router as model_loader_router,
)
from backend.ai.model_registry import ModelMetadata, ModelRegistry, get_model_registry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def loader() -> ModelLoader:
    return ModelLoader()


@pytest.fixture
def client(registry: ModelRegistry, loader: ModelLoader) -> TestClient:
    app = FastAPI()
    app.include_router(model_loader_router)
    app.dependency_overrides[get_model_registry] = lambda: registry
    app.dependency_overrides[get_model_loader] = lambda: loader
    return TestClient(app)


def register_valid_model(registry: ModelRegistry, name: str = "gpt-embed", version: str = "1.0.0"):
    return registry.register(
        name, version, ModelMetadata(provider="openai", entry_point="models.gpt_embed:Model")
    )


def test_discover_lists_unloaded_registered_models(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)

    manifests = loader.discover(registry)

    assert [manifest.name for manifest in manifests] == ["gpt-embed"]
    assert isinstance(manifests[0], ModelManifest)


def test_discover_excludes_already_loaded_models(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)
    loader.load("gpt-embed", registry=registry)

    manifests = loader.discover(registry)

    assert manifests == []


def test_load_returns_loaded_model(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)

    loaded = loader.load("gpt-embed", registry=registry)

    assert isinstance(loaded, LoadedModel)
    assert loaded.name == "gpt-embed"
    assert loaded.version == "1.0.0"
    assert loader.is_loaded("gpt-embed") is True


def test_load_is_lazy_and_returns_cached_instance(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)

    first = loader.load("gpt-embed", registry=registry)
    second = loader.load("gpt-embed", registry=registry)

    assert first is second


def test_load_rejects_model_without_entry_point(registry: ModelRegistry, loader: ModelLoader):
    registry.register("broken-model", "1.0.0", ModelMetadata(provider="openai"))

    with pytest.raises(ModelValidationError):
        loader.load("broken-model", registry=registry)


def test_load_unknown_model_raises(registry: ModelRegistry, loader: ModelLoader):
    from backend.ai.model_registry import UnknownModelError

    with pytest.raises(UnknownModelError):
        loader.load("does-not-exist", registry=registry)


def test_reload_refreshes_loaded_model(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)
    first = loader.load("gpt-embed", registry=registry)

    reloaded = loader.reload("gpt-embed", registry=registry)

    assert reloaded.name == "gpt-embed"
    assert reloaded is not first


def test_reload_not_loaded_raises(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)

    with pytest.raises(ModelNotLoadedError):
        loader.reload("gpt-embed", registry=registry)


def test_unload_removes_model(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry)
    loader.load("gpt-embed", registry=registry)

    loader.unload("gpt-embed")

    assert loader.is_loaded("gpt-embed") is False


def test_unload_not_loaded_raises(loader: ModelLoader):
    with pytest.raises(ModelNotLoadedError):
        loader.unload("gpt-embed")


def test_list_loaded_returns_all_loaded_models(registry: ModelRegistry, loader: ModelLoader):
    register_valid_model(registry, "gpt-embed")
    register_valid_model(registry, "gpt-chat")
    loader.load("gpt-embed", registry=registry)
    loader.load("gpt-chat", registry=registry)

    listed = loader.list_loaded()

    assert [model.name for model in listed] == ["gpt-chat", "gpt-embed"]


def test_api_load_and_list_loaded(client: TestClient, registry: ModelRegistry):
    register_valid_model(registry)

    response = client.post("/ai/models/load", json={"name": "gpt-embed"})
    assert response.status_code == 200
    assert response.json()["name"] == "gpt-embed"

    listed = client.get("/ai/models/loaded")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_load_unknown_model_returns_404(client: TestClient):
    response = client.post("/ai/models/load", json={"name": "does-not-exist"})

    assert response.status_code == 404


def test_api_load_invalid_manifest_returns_422(client: TestClient, registry: ModelRegistry):
    registry.register("broken-model", "1.0.0")

    response = client.post("/ai/models/load", json={"name": "broken-model"})

    assert response.status_code == 422


def test_api_reload_endpoint(client: TestClient, registry: ModelRegistry):
    register_valid_model(registry)
    client.post("/ai/models/load", json={"name": "gpt-embed"})

    response = client.post("/ai/models/reload/gpt-embed")

    assert response.status_code == 200
    assert response.json()["name"] == "gpt-embed"


def test_api_reload_not_loaded_returns_404(client: TestClient, registry: ModelRegistry):
    register_valid_model(registry)

    response = client.post("/ai/models/reload/gpt-embed")

    assert response.status_code == 404


def test_api_unload_endpoint(client: TestClient, registry: ModelRegistry):
    register_valid_model(registry)
    client.post("/ai/models/load", json={"name": "gpt-embed"})

    response = client.post("/ai/models/unload/gpt-embed")
    assert response.status_code == 204

    listed = client.get("/ai/models/loaded")
    assert listed.json() == []


def test_api_unload_not_loaded_returns_404(client: TestClient):
    response = client.post("/ai/models/unload/gpt-embed")

    assert response.status_code == 404
