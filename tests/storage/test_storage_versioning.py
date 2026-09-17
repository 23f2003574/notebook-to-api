import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactManager, ArtifactType
from backend.storage.object_storage import ObjectStorageEngine
from backend.storage.storage_versioning import (
    StorageVersion,
    StorageVersionManager,
    VersionSnapshot,
    get_storage_version_manager,
    router as storage_versioning_router,
)


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def artifact_manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def manager(object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager) -> StorageVersionManager:
    return StorageVersionManager(object_storage=object_storage, artifact_manager=artifact_manager)


@pytest.fixture
def client(manager: StorageVersionManager) -> TestClient:
    app = FastAPI()
    app.include_router(storage_versioning_router)
    app.dependency_overrides[get_storage_version_manager] = lambda: manager
    return TestClient(app)


def test_create_version_creates_first_version(manager: StorageVersionManager):
    version = manager.create_version("artifact-1", b"v1-data")

    assert isinstance(version, StorageVersion)
    assert version.artifact_id == "artifact-1"
    assert version.version_number == 1
    assert isinstance(version.snapshot, VersionSnapshot)
    assert version.snapshot.size == len(b"v1-data")


def test_create_version_rejects_empty_artifact_id(manager: StorageVersionManager):
    with pytest.raises(ValueError):
        manager.create_version("", b"data")


def test_create_version_increments_version_number(manager: StorageVersionManager):
    manager.create_version("artifact-1", b"v1-data")
    second = manager.create_version("artifact-1", b"v2-data")

    assert second.version_number == 2


def test_latest_returns_most_recent_version(manager: StorageVersionManager):
    manager.create_version("artifact-1", b"v1-data")
    manager.create_version("artifact-1", b"v2-data")

    latest = manager.latest("artifact-1")

    assert latest.version_number == 2


def test_latest_raises_for_unknown_artifact(manager: StorageVersionManager):
    with pytest.raises(KeyError):
        manager.latest("missing")


def test_history_returns_versions_in_order(manager: StorageVersionManager):
    manager.create_version("artifact-1", b"v1-data")
    manager.create_version("artifact-1", b"v2-data")
    manager.create_version("artifact-1", b"v3-data")

    versions = manager.history("artifact-1")

    assert [v.version_number for v in versions] == [1, 2, 3]


def test_history_returns_empty_for_unknown_artifact(manager: StorageVersionManager):
    assert manager.history("missing") == []


def test_rollback_creates_new_version_with_old_content(manager: StorageVersionManager, object_storage: ObjectStorageEngine):
    manager.create_version("artifact-1", b"v1-data")
    manager.create_version("artifact-1", b"v2-data")

    rolled_back = manager.rollback("artifact-1", 1)

    assert rolled_back.version_number == 3
    assert rolled_back.comment == "rollback to v1"
    stored = object_storage.get(rolled_back.object_key)
    assert stored.data == b"v1-data"

    history = manager.history("artifact-1")
    assert len(history) == 3
    assert history[0].object_key != rolled_back.object_key


def test_rollback_raises_for_unknown_version(manager: StorageVersionManager):
    manager.create_version("artifact-1", b"v1-data")

    with pytest.raises(KeyError):
        manager.rollback("artifact-1", 99)


def test_rollback_raises_for_unknown_artifact(manager: StorageVersionManager):
    with pytest.raises(KeyError):
        manager.rollback("missing", 1)


def test_create_version_syncs_artifact_manifest(
    manager: StorageVersionManager, artifact_manager: ArtifactManager
):
    artifact = artifact_manager.create("model.pkl", ArtifactType.MODEL, b"initial")

    version = manager.create_version(artifact.artifact_id, b"updated-data")

    synced = artifact_manager.fetch(artifact.artifact_id)
    assert synced.object_key == version.object_key
    assert synced.manifest.checksum == version.snapshot.checksum


def test_create_version_without_artifact_manager_still_works(object_storage: ObjectStorageEngine):
    manager = StorageVersionManager(object_storage=object_storage)

    version = manager.create_version("standalone-artifact", b"data")

    assert version.version_number == 1


def test_api_create_and_fetch_latest_version(client: TestClient):
    create_response = client.post(
        "/storage/versions",
        params={"artifact_id": "artifact-1"},
        content=b"v1-data",
    )
    assert create_response.status_code == 200
    assert create_response.json()["version_number"] == 1

    latest_response = client.get("/storage/versions/artifact-1/latest")
    assert latest_response.status_code == 200
    assert latest_response.json()["version_number"] == 1


def test_api_latest_missing_returns_404(client: TestClient):
    response = client.get("/storage/versions/missing/latest")
    assert response.status_code == 404


def test_api_history_returns_all_versions(client: TestClient):
    client.post("/storage/versions", params={"artifact_id": "artifact-1"}, content=b"v1")
    client.post("/storage/versions", params={"artifact_id": "artifact-1"}, content=b"v2")

    response = client.get("/storage/versions/artifact-1")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_rollback_workflow(client: TestClient):
    client.post("/storage/versions", params={"artifact_id": "artifact-1"}, content=b"v1")
    client.post("/storage/versions", params={"artifact_id": "artifact-1"}, content=b"v2")

    rollback_response = client.post(
        "/storage/versions/artifact-1/rollback",
        params={"version_number": 1},
    )

    assert rollback_response.status_code == 200
    assert rollback_response.json()["version_number"] == 3


def test_api_rollback_missing_version_returns_404(client: TestClient):
    client.post("/storage/versions", params={"artifact_id": "artifact-1"}, content=b"v1")

    response = client.post(
        "/storage/versions/artifact-1/rollback",
        params={"version_number": 99},
    )

    assert response.status_code == 404
