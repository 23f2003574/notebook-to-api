from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import ArtifactMetadata, ArtifactRegistry, UnknownArtifactError
from backend.governance.artifact_versioning import (
    ArtifactVersion,
    ArtifactVersionManager,
    DuplicateVersionError,
    NoRollbackTargetError,
    UnknownVersionError,
    router as artifact_versioning_router,
)
from backend.governance.artifact_registry import router as artifact_registry_router

BASE_TIME = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)


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
def manager(registry: ArtifactRegistry) -> ArtifactVersionManager:
    return ArtifactVersionManager(registry=registry)


def _publish(registry: ArtifactRegistry, name: str, version: str):
    return registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=BASE_TIME
    )


def test_create_records_version(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")

    entry = manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)

    assert isinstance(entry, ArtifactVersion)
    assert entry.state == "ACTIVE"
    assert entry.version == "1.0.0"


def test_create_requires_name(manager: ArtifactVersionManager):
    with pytest.raises(ValueError):
        manager.create("", "1.0.0")


def test_create_requires_semver_format(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "v1")

    with pytest.raises(ValueError):
        manager.create("svc-a", "v1")


def test_create_requires_published_artifact(manager: ArtifactVersionManager):
    with pytest.raises(UnknownArtifactError):
        manager.create("svc-a", "1.0.0")


def test_create_duplicate_version_raises(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)

    with pytest.raises(DuplicateVersionError):
        manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)


def test_latest_returns_highest_semver(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    _publish(registry, "svc-a", "1.2.0")
    _publish(registry, "svc-a", "1.10.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)
    manager.create("svc-a", "1.2.0", timestamp=BASE_TIME)
    manager.create("svc-a", "1.10.0", timestamp=BASE_TIME)

    latest = manager.latest("svc-a")

    assert latest.version == "1.10.0"


def test_latest_raises_when_no_versions(manager: ArtifactVersionManager):
    with pytest.raises(UnknownVersionError):
        manager.latest("svc-a")


def test_history_returns_versions_in_semver_order(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "2.0.0")
    _publish(registry, "svc-a", "1.0.0")
    manager.create("svc-a", "2.0.0", timestamp=BASE_TIME)
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)

    history = manager.history("svc-a")

    assert [entry.version for entry in history] == ["1.0.0", "2.0.0"]


def test_rollback_defaults_to_previous_active_version(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    _publish(registry, "svc-a", "2.0.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)
    manager.create("svc-a", "2.0.0", timestamp=BASE_TIME)

    target = manager.rollback("svc-a", timestamp=BASE_TIME)

    assert target.version == "1.0.0"
    assert manager.latest("svc-a").version == "1.0.0"


def test_rollback_to_explicit_target(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    _publish(registry, "svc-a", "1.5.0")
    _publish(registry, "svc-a", "2.0.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)
    manager.create("svc-a", "1.5.0", timestamp=BASE_TIME)
    manager.create("svc-a", "2.0.0", timestamp=BASE_TIME)

    target = manager.rollback("svc-a", to_version="1.0.0", timestamp=BASE_TIME)

    assert target.version == "1.0.0"


def test_rollback_unknown_target_raises(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)

    with pytest.raises(UnknownVersionError):
        manager.rollback("svc-a", to_version="9.9.9", timestamp=BASE_TIME)


def test_rollback_with_no_other_versions_raises(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)

    with pytest.raises(NoRollbackTargetError):
        manager.rollback("svc-a", timestamp=BASE_TIME)


def test_rolled_back_version_excluded_from_latest(registry: ArtifactRegistry, manager: ArtifactVersionManager):
    _publish(registry, "svc-a", "1.0.0")
    _publish(registry, "svc-a", "2.0.0")
    manager.create("svc-a", "1.0.0", timestamp=BASE_TIME)
    manager.create("svc-a", "2.0.0", timestamp=BASE_TIME)

    manager.rollback("svc-a", timestamp=BASE_TIME)
    history = manager.history("svc-a")
    rolled_back = next(entry for entry in history if entry.version == "2.0.0")

    assert rolled_back.state == "ROLLED_BACK"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    return TestClient(app)


def _publish_via_api(client: TestClient, name: str, version: str) -> None:
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


def test_api_create_and_list_versions(client: TestClient):
    _publish_via_api(client, "svc-api-a", "1.0.0")

    create_response = client.post(
        "/governance/artifacts/svc-api-a/versions", json={"version": "1.0.0"}
    )
    list_response = client.get("/governance/artifacts/svc-api-a/versions")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_api_create_missing_version_returns_422(client: TestClient):
    response = client.post("/governance/artifacts/svc-api-a/versions", json={})

    assert response.status_code == 422


def test_api_create_unpublished_artifact_returns_404(client: TestClient):
    response = client.post(
        "/governance/artifacts/svc-api-missing/versions", json={"version": "1.0.0"}
    )

    assert response.status_code == 404


def test_api_latest_version(client: TestClient):
    _publish_via_api(client, "svc-api-b", "1.0.0")
    _publish_via_api(client, "svc-api-b", "2.0.0")
    client.post("/governance/artifacts/svc-api-b/versions", json={"version": "1.0.0"})
    client.post("/governance/artifacts/svc-api-b/versions", json={"version": "2.0.0"})

    response = client.get("/governance/artifacts/svc-api-b/versions/latest")

    assert response.status_code == 200
    assert response.json()["version"] == "2.0.0"


def test_api_latest_returns_404_when_no_versions(client: TestClient):
    response = client.get("/governance/artifacts/svc-api-none/versions/latest")

    assert response.status_code == 404
