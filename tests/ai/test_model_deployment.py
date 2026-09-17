import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.model_deployment import (
    Deployment,
    DeploymentTarget,
    InvalidDeploymentStateError,
    ModelDeploymentManager,
    UnknownDeploymentError,
    get_model_deployment_manager,
    router as model_deployment_router,
)
from backend.ai.model_registry import ModelRegistry, UnknownModelError, get_model_registry
from backend.ai.model_versioning import ModelVersionManager, get_model_version_manager


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def versions() -> ModelVersionManager:
    return ModelVersionManager()


@pytest.fixture
def manager() -> ModelDeploymentManager:
    return ModelDeploymentManager()


@pytest.fixture
def client(registry: ModelRegistry, versions: ModelVersionManager, manager: ModelDeploymentManager) -> TestClient:
    app = FastAPI()
    app.include_router(model_deployment_router)
    app.dependency_overrides[get_model_registry] = lambda: registry
    app.dependency_overrides[get_model_version_manager] = lambda: versions
    app.dependency_overrides[get_model_deployment_manager] = lambda: manager
    return TestClient(app)


def test_deploy_creates_development_deployment_by_default(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")

    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry)

    assert isinstance(deployment, Deployment)
    assert deployment.target == DeploymentTarget.DEVELOPMENT
    assert deployment.last_action == "deployed"


def test_deploy_uses_active_version_when_not_specified(
    registry: ModelRegistry, versions: ModelVersionManager, manager: ModelDeploymentManager
):
    versions.create("gpt-a", "1.0.0", registry=registry)
    versions.create("gpt-a", "2.0.0", registry=registry)

    deployment = manager.deploy("gpt-a", registry=registry, versions=versions)

    assert deployment.version == "2.0.0"


def test_deploy_without_version_or_version_manager_raises(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")

    with pytest.raises(ValueError):
        manager.deploy("gpt-a", registry=registry)


def test_deploy_unregistered_model_raises(registry: ModelRegistry, manager: ModelDeploymentManager):
    with pytest.raises(UnknownModelError):
        manager.deploy("does-not-exist", "1.0.0", registry=registry)


def test_deploy_rejects_invalid_target(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")

    with pytest.raises(ValueError):
        manager.deploy("gpt-a", "1.0.0", registry=registry, target="bogus")


def test_deploy_to_explicit_target(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")

    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry, target="staging")

    assert deployment.target == DeploymentTarget.STAGING


def test_promote_advances_through_stages(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry)

    staging = manager.promote(deployment.deployment_id)
    canary = manager.promote(deployment.deployment_id)
    production = manager.promote(deployment.deployment_id)

    assert staging.target == DeploymentTarget.STAGING
    assert canary.target == DeploymentTarget.CANARY
    assert production.target == DeploymentTarget.PRODUCTION
    assert production.last_action == "promoted"


def test_promote_at_final_stage_raises(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry, target="production")

    with pytest.raises(InvalidDeploymentStateError):
        manager.promote(deployment.deployment_id)


def test_promote_unknown_deployment_raises(manager: ModelDeploymentManager):
    with pytest.raises(UnknownDeploymentError):
        manager.promote("does-not-exist")


def test_rollback_reverts_to_previous_stage(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry)
    manager.promote(deployment.deployment_id)
    manager.promote(deployment.deployment_id)

    rolled_back = manager.rollback(deployment.deployment_id)

    assert rolled_back.target == DeploymentTarget.STAGING
    assert rolled_back.last_action == "rolled_back"


def test_rollback_with_no_earlier_stage_raises(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry)

    with pytest.raises(InvalidDeploymentStateError):
        manager.rollback(deployment.deployment_id)


def test_rollback_unknown_deployment_raises(manager: ModelDeploymentManager):
    with pytest.raises(UnknownDeploymentError):
        manager.rollback("does-not-exist")


def test_status_returns_deployment(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    deployment = manager.deploy("gpt-a", "1.0.0", registry=registry)

    fetched = manager.status(deployment.deployment_id)

    assert fetched.deployment_id == deployment.deployment_id


def test_status_unknown_deployment_raises(manager: ModelDeploymentManager):
    with pytest.raises(UnknownDeploymentError):
        manager.status("does-not-exist")


def test_list_deployments_returns_all(registry: ModelRegistry, manager: ModelDeploymentManager):
    registry.register("gpt-a", "1.0.0")
    first = manager.deploy("gpt-a", "1.0.0", registry=registry, target="development")
    second = manager.deploy("gpt-a", "1.0.0", registry=registry, target="staging")

    listed = manager.list_deployments()

    assert [deployment.deployment_id for deployment in listed] == [first.deployment_id, second.deployment_id]


def test_api_deploy(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")

    response = client.post("/ai/deployments", json={"model_name": "gpt-a", "version": "1.0.0"})

    assert response.status_code == 201
    assert response.json()["target"] == "development"


def test_api_deploy_unknown_model_returns_404(client: TestClient):
    response = client.post("/ai/deployments", json={"model_name": "does-not-exist", "version": "1.0.0"})

    assert response.status_code == 404


def test_api_deploy_missing_version_falls_back_to_version_manager(client: TestClient, registry: ModelRegistry):
    # The API always wires a ModelVersionManager, so an omitted version resolves via
    # active_version() rather than raising the "no version manager" ValueError directly.
    registry.register("gpt-a", "1.0.0")

    response = client.post("/ai/deployments", json={"model_name": "gpt-a"})

    assert response.status_code == 404


def test_api_promote(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    submitted = client.post("/ai/deployments", json={"model_name": "gpt-a", "version": "1.0.0"})
    deployment_id = submitted.json()["deployment_id"]

    response = client.post(f"/ai/deployments/{deployment_id}/promote")

    assert response.status_code == 200
    assert response.json()["target"] == "staging"


def test_api_promote_unknown_returns_404(client: TestClient):
    response = client.post("/ai/deployments/does-not-exist/promote")

    assert response.status_code == 404


def test_api_promote_final_stage_returns_409(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    submitted = client.post(
        "/ai/deployments", json={"model_name": "gpt-a", "version": "1.0.0", "target": "production"}
    )
    deployment_id = submitted.json()["deployment_id"]

    response = client.post(f"/ai/deployments/{deployment_id}/promote")

    assert response.status_code == 409


def test_api_rollback(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    submitted = client.post("/ai/deployments", json={"model_name": "gpt-a", "version": "1.0.0"})
    deployment_id = submitted.json()["deployment_id"]
    client.post(f"/ai/deployments/{deployment_id}/promote")

    response = client.post(f"/ai/deployments/{deployment_id}/rollback")

    assert response.status_code == 200
    assert response.json()["target"] == "development"


def test_api_rollback_no_earlier_stage_returns_409(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    submitted = client.post("/ai/deployments", json={"model_name": "gpt-a", "version": "1.0.0"})
    deployment_id = submitted.json()["deployment_id"]

    response = client.post(f"/ai/deployments/{deployment_id}/rollback")

    assert response.status_code == 409


def test_api_get_deployment_status(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    submitted = client.post("/ai/deployments", json={"model_name": "gpt-a", "version": "1.0.0"})
    deployment_id = submitted.json()["deployment_id"]

    response = client.get(f"/ai/deployments/{deployment_id}")

    assert response.status_code == 200
    assert response.json()["deployment_id"] == deployment_id


def test_api_get_deployment_unknown_returns_404(client: TestClient):
    response = client.get("/ai/deployments/does-not-exist")

    assert response.status_code == 404
