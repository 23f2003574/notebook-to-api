from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import (
    ArtifactMetadata,
    ArtifactRegistry,
    router as artifact_registry_router,
)
from backend.governance.artifact_versioning import (
    ArtifactVersionManager,
    router as artifact_versioning_router,
)
from backend.governance.artifact_promotion import (
    ArtifactPromotionEngine,
    InvalidPromotionError,
    PromotionPolicyError,
    PromotionRecord,
    router as artifact_promotion_router,
)
from backend.governance.artifact_versioning import UnknownVersionError

BASE_TIME = datetime(2026, 7, 27, 11, 0, 0, tzinfo=timezone.utc)


def _metadata() -> ArtifactMetadata:
    return ArtifactMetadata(
        content_type="application/octet-stream",
        size_bytes=1024,
        checksum="a" * 64,
        checksum_algorithm="sha256",
    )


@pytest.fixture
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


@pytest.fixture
def version_manager(registry: ArtifactRegistry) -> ArtifactVersionManager:
    return ArtifactVersionManager(registry=registry)


@pytest.fixture
def engine(version_manager: ArtifactVersionManager) -> ArtifactPromotionEngine:
    return ArtifactPromotionEngine(version_manager=version_manager)


def _prepare_version(registry: ArtifactRegistry, version_manager: ArtifactVersionManager, name: str, version: str):
    registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=BASE_TIME
    )
    return version_manager.create(name, version, timestamp=BASE_TIME)


def test_promote_moves_to_next_environment(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    record = engine.promote("svc-a", "1.0.0", "Staging", timestamp=BASE_TIME)

    assert isinstance(record, PromotionRecord)
    assert record.from_environment == "Development"
    assert record.to_environment == "Staging"
    assert record.action == "PROMOTE"


def test_promote_requires_registered_version(engine: ArtifactPromotionEngine):
    with pytest.raises(UnknownVersionError):
        engine.promote("svc-a", "1.0.0", "Staging")


def test_promote_rejects_unknown_environment(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    with pytest.raises(InvalidPromotionError):
        engine.promote("svc-a", "1.0.0", "Canary")


def test_promote_rejects_skipping_a_stage(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    with pytest.raises(PromotionPolicyError):
        engine.promote("svc-a", "1.0.0", "Production")


def test_promote_rejects_rolled_back_version(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")
    _prepare_version(registry, version_manager, "svc-a", "2.0.0")
    version_manager.rollback("svc-a", timestamp=BASE_TIME)

    with pytest.raises(InvalidPromotionError):
        engine.promote("svc-a", "2.0.0", "Staging")


def test_promote_through_full_pipeline(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    engine.promote("svc-a", "1.0.0", "Staging", timestamp=BASE_TIME)
    record = engine.promote("svc-a", "1.0.0", "Production", timestamp=BASE_TIME)

    assert record.from_environment == "Staging"
    assert record.to_environment == "Production"


def test_validate_does_not_mutate_state(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    engine.validate("svc-a", "1.0.0", "Staging")

    assert engine.history("svc-a") == []


def test_history_returns_promotions_in_order(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    engine.promote("svc-a", "1.0.0", "Staging", timestamp=BASE_TIME)
    engine.promote("svc-a", "1.0.0", "Production", timestamp=BASE_TIME)

    history = engine.history("svc-a")

    assert [record.to_environment for record in history] == ["Staging", "Production"]


def test_history_empty_when_never_promoted(engine: ArtifactPromotionEngine):
    assert engine.history("svc-unknown") == []


def test_rollback_moves_to_previous_environment(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")
    engine.promote("svc-a", "1.0.0", "Staging", timestamp=BASE_TIME)
    engine.promote("svc-a", "1.0.0", "Production", timestamp=BASE_TIME)

    record = engine.rollback("svc-a", "1.0.0", timestamp=BASE_TIME)

    assert record.action == "ROLLBACK"
    assert record.from_environment == "Production"
    assert record.to_environment == "Staging"


def test_rollback_at_development_raises(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")

    with pytest.raises(PromotionPolicyError):
        engine.rollback("svc-a", "1.0.0", timestamp=BASE_TIME)


def test_rollback_recorded_in_history(
    registry: ArtifactRegistry, version_manager: ArtifactVersionManager, engine: ArtifactPromotionEngine
):
    _prepare_version(registry, version_manager, "svc-a", "1.0.0")
    engine.promote("svc-a", "1.0.0", "Staging", timestamp=BASE_TIME)

    engine.rollback("svc-a", "1.0.0", timestamp=BASE_TIME)

    history = engine.history("svc-a")
    assert [record.action for record in history] == ["PROMOTE", "ROLLBACK"]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    return TestClient(app)


def _publish_and_register(client: TestClient, name: str, version: str) -> None:
    client.post(
        "/governance/artifacts",
        json={
            "name": name,
            "version": version,
            "location": f"loc-{name}-{version}",
            "metadata": {
                "content_type": "application/octet-stream",
                "size_bytes": 1024,
                "checksum": "a" * 64,
                "checksum_algorithm": "sha256",
            },
        },
    )
    client.post(f"/governance/artifacts/{name}/versions", json={"version": version})


def test_api_promote_and_list(client: TestClient):
    _publish_and_register(client, "svc-promo-a", "1.0.0")

    promote_response = client.post(
        "/governance/artifacts/svc-promo-a/promote",
        json={"version": "1.0.0", "target_environment": "Staging"},
    )
    list_response = client.get("/governance/artifacts/svc-promo-a/promotions")

    assert promote_response.status_code == 200
    assert promote_response.json()["to_environment"] == "Staging"
    assert len(list_response.json()) == 1


def test_api_promote_missing_fields_returns_422(client: TestClient):
    response = client.post("/governance/artifacts/svc-promo-a/promote", json={})

    assert response.status_code == 422


def test_api_promote_unknown_version_returns_404(client: TestClient):
    response = client.post(
        "/governance/artifacts/svc-promo-missing/promote",
        json={"version": "1.0.0", "target_environment": "Staging"},
    )

    assert response.status_code == 404


def test_api_promote_skip_stage_returns_409(client: TestClient):
    _publish_and_register(client, "svc-promo-b", "1.0.0")

    response = client.post(
        "/governance/artifacts/svc-promo-b/promote",
        json={"version": "1.0.0", "target_environment": "Production"},
    )

    assert response.status_code == 409


def test_api_promote_invalid_environment_returns_422(client: TestClient):
    _publish_and_register(client, "svc-promo-c", "1.0.0")

    response = client.post(
        "/governance/artifacts/svc-promo-c/promote",
        json={"version": "1.0.0", "target_environment": "Canary"},
    )

    assert response.status_code == 422
