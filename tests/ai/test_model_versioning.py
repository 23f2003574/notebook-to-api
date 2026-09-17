import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.model_registry import ModelRegistry, UnknownModelError, get_model_registry
from backend.ai.model_versioning import (
    InvalidVersionError,
    ModelVersion,
    ModelVersionManager,
    VersionNotFoundError,
    get_model_version_manager,
    router as model_versioning_router,
)


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def manager() -> ModelVersionManager:
    return ModelVersionManager()


@pytest.fixture
def client(registry: ModelRegistry, manager: ModelVersionManager) -> TestClient:
    app = FastAPI()
    app.include_router(model_versioning_router)
    app.dependency_overrides[get_model_registry] = lambda: registry
    app.dependency_overrides[get_model_version_manager] = lambda: manager
    return TestClient(app)


def test_create_registers_first_version_as_active(registry: ModelRegistry, manager: ModelVersionManager):
    version = manager.create("gpt-embed", "1.0.0", registry=registry)

    assert isinstance(version, ModelVersion)
    assert version.version == "1.0.0"
    assert version.is_active is True


def test_create_rejects_invalid_semver(registry: ModelRegistry, manager: ModelVersionManager):
    with pytest.raises(InvalidVersionError):
        manager.create("gpt-embed", "not-a-version", registry=registry)


def test_create_rejects_non_increasing_version(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)

    with pytest.raises(InvalidVersionError):
        manager.create("gpt-embed", "1.0.0", registry=registry)

    with pytest.raises(InvalidVersionError):
        manager.create("gpt-embed", "0.9.0", registry=registry)


def test_create_allows_increasing_version(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)
    second = manager.create("gpt-embed", "1.1.0", registry=registry)

    assert second.version == "1.1.0"
    assert second.is_active is True


def test_latest_resolves_highest_semver_not_last_registered(
    registry: ModelRegistry, manager: ModelVersionManager
):
    # Registered directly (out of semver order) to prove latest() sorts by
    # version number rather than by registration/insertion order.
    registry.register("gpt-embed", "1.0.0")
    registry.register("gpt-embed", "2.0.0")
    registry.register("gpt-embed", "1.5.0")

    latest = manager.latest("gpt-embed", registry=registry)

    assert latest.version == "2.0.0"


def test_latest_reflects_active_flag(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)
    manager.create("gpt-embed", "2.0.0", registry=registry)
    manager.rollback("gpt-embed", "1.0.0", registry=registry)

    latest = manager.latest("gpt-embed", registry=registry)

    assert latest.version == "2.0.0"
    assert latest.is_active is False


def test_latest_unknown_model_raises(registry: ModelRegistry, manager: ModelVersionManager):
    with pytest.raises(UnknownModelError):
        manager.latest("does-not-exist", registry=registry)


def test_rollback_to_explicit_target(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)
    manager.create("gpt-embed", "2.0.0", registry=registry)

    rolled_back = manager.rollback("gpt-embed", "1.0.0", registry=registry)

    assert rolled_back.version == "1.0.0"
    assert rolled_back.is_active is True


def test_rollback_without_target_goes_to_previous_version(
    registry: ModelRegistry, manager: ModelVersionManager
):
    manager.create("gpt-embed", "1.0.0", registry=registry)
    manager.create("gpt-embed", "2.0.0", registry=registry)

    rolled_back = manager.rollback("gpt-embed", registry=registry)

    assert rolled_back.version == "1.0.0"


def test_rollback_unknown_target_raises(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)

    with pytest.raises(VersionNotFoundError):
        manager.rollback("gpt-embed", "9.9.9", registry=registry)


def test_rollback_with_no_earlier_version_raises(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)

    with pytest.raises(ValueError):
        manager.rollback("gpt-embed", registry=registry)


def test_rollback_unknown_model_raises(registry: ModelRegistry, manager: ModelVersionManager):
    with pytest.raises(UnknownModelError):
        manager.rollback("does-not-exist", registry=registry)


def test_history_returns_create_and_rollback_events(registry: ModelRegistry, manager: ModelVersionManager):
    manager.create("gpt-embed", "1.0.0", registry=registry)
    manager.create("gpt-embed", "2.0.0", registry=registry)
    manager.rollback("gpt-embed", "1.0.0", registry=registry)

    records = manager.history("gpt-embed")

    assert [record.action for record in records] == ["created", "created", "rollback"]
    assert [record.version for record in records] == ["1.0.0", "2.0.0", "1.0.0"]


def test_history_unknown_model_raises(manager: ModelVersionManager):
    with pytest.raises(UnknownModelError):
        manager.history("does-not-exist")


def test_api_create_version(client: TestClient):
    response = client.post("/ai/models/gpt-embed/versions", json={"version": "1.0.0"})

    assert response.status_code == 201
    assert response.json()["version"] == "1.0.0"
    assert response.json()["is_active"] is True


def test_api_create_invalid_version_returns_422(client: TestClient):
    response = client.post("/ai/models/gpt-embed/versions", json={"version": "bogus"})

    assert response.status_code == 422


def test_api_list_version_history(client: TestClient):
    client.post("/ai/models/gpt-embed/versions", json={"version": "1.0.0"})
    client.post("/ai/models/gpt-embed/versions", json={"version": "2.0.0"})

    response = client.get("/ai/models/gpt-embed/versions")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_list_version_history_unknown_model_returns_404(client: TestClient):
    response = client.get("/ai/models/does-not-exist/versions")

    assert response.status_code == 404


def test_api_latest_version(client: TestClient):
    client.post("/ai/models/gpt-embed/versions", json={"version": "1.0.0"})
    client.post("/ai/models/gpt-embed/versions", json={"version": "2.0.0"})

    response = client.get("/ai/models/gpt-embed/versions/latest")

    assert response.status_code == 200
    assert response.json()["version"] == "2.0.0"


def test_api_rollback(client: TestClient):
    client.post("/ai/models/gpt-embed/versions", json={"version": "1.0.0"})
    client.post("/ai/models/gpt-embed/versions", json={"version": "2.0.0"})

    response = client.post("/ai/models/gpt-embed/rollback", json={"version": "1.0.0"})

    assert response.status_code == 200
    assert response.json()["version"] == "1.0.0"
    assert response.json()["is_active"] is True


def test_api_rollback_unknown_target_returns_404(client: TestClient):
    client.post("/ai/models/gpt-embed/versions", json={"version": "1.0.0"})

    response = client.post("/ai/models/gpt-embed/rollback", json={"version": "9.9.9"})

    assert response.status_code == 404
