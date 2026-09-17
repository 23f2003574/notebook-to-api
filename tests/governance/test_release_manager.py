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
    router as artifact_promotion_router,
)
from backend.governance.release_manager import (
    ArtifactNotProductionReadyError,
    InvalidReleaseStateError,
    Release,
    ReleaseManager,
    UnknownReleaseError,
    router as release_manager_router,
)

BASE_TIME = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


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
def promotion_engine(version_manager: ArtifactVersionManager) -> ArtifactPromotionEngine:
    return ArtifactPromotionEngine(version_manager=version_manager)


@pytest.fixture
def manager(promotion_engine: ArtifactPromotionEngine) -> ReleaseManager:
    return ReleaseManager(promotion_engine=promotion_engine)


def _promote_to_production(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    name: str,
    version: str,
) -> None:
    registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=BASE_TIME
    )
    version_manager.create(name, version, timestamp=BASE_TIME)
    promotion_engine.promote(name, version, "Staging", timestamp=BASE_TIME)
    promotion_engine.promote(name, version, "Production", timestamp=BASE_TIME)


def test_create_release_with_production_artifact(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")

    release = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )

    assert isinstance(release, Release)
    assert release.state == "DRAFT"
    assert release.artifacts == ({"name": "svc-a", "version": "1.0.0"},)


def test_create_requires_name(manager: ReleaseManager):
    with pytest.raises(ValueError):
        manager.create("", [{"name": "svc-a", "version": "1.0.0"}])


def test_create_requires_at_least_one_artifact(manager: ReleaseManager):
    with pytest.raises(ValueError):
        manager.create("release-1", [])


def test_create_requires_artifact_name_and_version(manager: ReleaseManager):
    with pytest.raises(ValueError):
        manager.create("release-1", [{"name": "svc-a"}])


def test_create_rejects_artifact_not_in_production(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    registry.publish("svc-a", "1.0.0", location="loc", metadata=_metadata(), timestamp=BASE_TIME)
    version_manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)

    with pytest.raises(ArtifactNotProductionReadyError):
        manager.create("release-1", [{"name": "svc-a", "version": "1.0.0"}])


def test_create_rejects_unpromoted_artifact_entirely(manager: ReleaseManager):
    with pytest.raises(ArtifactNotProductionReadyError):
        manager.create("release-1", [{"name": "svc-never-promoted", "version": "1.0.0"}])


def test_get_returns_created_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")
    release = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )

    assert manager.get(release.release_id) == release


def test_get_unknown_raises(manager: ReleaseManager):
    with pytest.raises(UnknownReleaseError):
        manager.get("does-not-exist")


def test_publish_transitions_to_published(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")
    release = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )

    updated = manager.publish(release.release_id, timestamp=BASE_TIME)

    assert updated.state == "PUBLISHED"
    assert updated.published_at == BASE_TIME


def test_publish_already_published_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")
    release = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )
    manager.publish(release.release_id, timestamp=BASE_TIME)

    with pytest.raises(InvalidReleaseStateError):
        manager.publish(release.release_id, timestamp=BASE_TIME)


def test_publish_unknown_raises(manager: ReleaseManager):
    with pytest.raises(UnknownReleaseError):
        manager.publish("does-not-exist")


def test_cancel_transitions_to_cancelled(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")
    release = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )

    updated = manager.cancel(release.release_id, timestamp=BASE_TIME)

    assert updated.state == "CANCELLED"
    assert updated.cancelled_at == BASE_TIME


def test_cancel_published_release_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")
    release = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )
    manager.publish(release.release_id, timestamp=BASE_TIME)

    with pytest.raises(InvalidReleaseStateError):
        manager.cancel(release.release_id, timestamp=BASE_TIME)


def test_history_returns_releases_in_creation_order(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    manager: ReleaseManager,
):
    _promote_to_production(registry, version_manager, promotion_engine, "svc-a", "1.0.0")
    _promote_to_production(registry, version_manager, promotion_engine, "svc-b", "1.0.0")

    first = manager.create(
        "release-1", [{"name": "svc-a", "version": "1.0.0"}], timestamp=BASE_TIME
    )
    second = manager.create(
        "release-2", [{"name": "svc-b", "version": "1.0.0"}], timestamp=BASE_TIME
    )

    history = manager.history()

    assert [release.release_id for release in history] == [first.release_id, second.release_id]


def test_history_empty_initially(manager: ReleaseManager):
    assert manager.history() == []


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(release_manager_router)
    return TestClient(app)


def _publish_promote_via_api(client: TestClient, name: str, version: str) -> None:
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
    client.post(
        f"/governance/artifacts/{name}/promote",
        json={"version": version, "target_environment": "Staging"},
    )
    client.post(
        f"/governance/artifacts/{name}/promote",
        json={"version": version, "target_environment": "Production"},
    )


def test_api_create_publish_and_get(client: TestClient):
    _publish_promote_via_api(client, "svc-rel-a", "1.0.0")

    create_response = client.post(
        "/governance/releases",
        json={"name": "release-api-1", "artifacts": [{"name": "svc-rel-a", "version": "1.0.0"}]},
    )
    release_id = create_response.json()["release_id"]

    publish_response = client.post(f"/governance/releases/{release_id}/publish")
    get_response = client.get(f"/governance/releases/{release_id}")

    assert create_response.status_code == 200
    assert publish_response.status_code == 200
    assert publish_response.json()["state"] == "PUBLISHED"
    assert get_response.status_code == 200


def test_api_create_missing_fields_returns_422(client: TestClient):
    response = client.post("/governance/releases", json={})

    assert response.status_code == 422


def test_api_create_not_production_ready_returns_409(client: TestClient):
    response = client.post(
        "/governance/releases",
        json={"name": "release-api-2", "artifacts": [{"name": "svc-rel-never", "version": "1.0.0"}]},
    )

    assert response.status_code == 409


def test_api_publish_unknown_release_returns_404(client: TestClient):
    response = client.post("/governance/releases/does-not-exist/publish")

    assert response.status_code == 404


def test_api_list_releases(client: TestClient):
    _publish_promote_via_api(client, "svc-rel-b", "1.0.0")
    client.post(
        "/governance/releases",
        json={"name": "release-api-3", "artifacts": [{"name": "svc-rel-b", "version": "1.0.0"}]},
    )

    response = client.get("/governance/releases")

    assert response.status_code == 200
    assert any(release["name"] == "release-api-3" for release in response.json())


def test_api_get_unknown_release_returns_404(client: TestClient):
    response = client.get("/governance/releases/does-not-exist")

    assert response.status_code == 404
