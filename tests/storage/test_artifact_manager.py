import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import (
    Artifact,
    ArtifactManager,
    ArtifactManifest,
    ArtifactType,
    get_artifact_manager,
    router as artifact_manager_router,
)
from backend.storage.object_storage import ObjectStorageEngine


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def client(manager: ArtifactManager) -> TestClient:
    app = FastAPI()
    app.include_router(artifact_manager_router)
    app.dependency_overrides[get_artifact_manager] = lambda: manager
    return TestClient(app)


def test_create_creates_artifact(manager: ArtifactManager):
    artifact = manager.create("model.pkl", ArtifactType.MODEL, b"binary-data")

    assert isinstance(artifact, Artifact)
    assert artifact.name == "model.pkl"
    assert artifact.artifact_type == ArtifactType.MODEL
    assert artifact.namespace == "default"
    assert isinstance(artifact.manifest, ArtifactManifest)
    assert artifact.manifest.size == len(b"binary-data")


def test_create_rejects_empty_name(manager: ArtifactManager):
    with pytest.raises(ValueError):
        manager.create("", ArtifactType.MODEL, b"data")


def test_create_stores_payload_in_object_storage(manager: ArtifactManager, object_storage: ObjectStorageEngine):
    artifact = manager.create("notebook.ipynb", ArtifactType.NOTEBOOK, b"{}")

    stored = object_storage.get(artifact.object_key)

    assert stored is not None
    assert stored.data == b"{}"


def test_fetch_returns_created_artifact(manager: ArtifactManager):
    created = manager.create("dataset.csv", ArtifactType.DATASET, b"a,b,c")

    fetched = manager.fetch(created.artifact_id)

    assert fetched is not None
    assert fetched.artifact_id == created.artifact_id


def test_fetch_returns_none_for_missing_artifact(manager: ArtifactManager):
    assert manager.fetch("missing") is None


def test_list_artifacts_filters_by_namespace(manager: ArtifactManager):
    manager.create("a.ipynb", ArtifactType.NOTEBOOK, b"1", namespace="team-a")
    manager.create("b.ipynb", ArtifactType.NOTEBOOK, b"2", namespace="team-b")

    team_a = manager.list_artifacts(namespace="team-a")

    assert len(team_a) == 1
    assert team_a[0].namespace == "team-a"


def test_list_artifacts_filters_by_type(manager: ArtifactManager):
    manager.create("a.ipynb", ArtifactType.NOTEBOOK, b"1")
    manager.create("a.pkl", ArtifactType.MODEL, b"2")

    models = manager.list_artifacts(artifact_type=ArtifactType.MODEL)

    assert len(models) == 1
    assert models[0].artifact_type == ArtifactType.MODEL


def test_move_updates_namespace_and_object_key(manager: ArtifactManager, object_storage: ObjectStorageEngine):
    artifact = manager.create("a.ipynb", ArtifactType.NOTEBOOK, b"data", namespace="team-a")
    old_key = artifact.object_key

    moved = manager.move(artifact.artifact_id, "team-b")

    assert moved.namespace == "team-b"
    assert moved.object_key != old_key
    assert object_storage.exists(old_key) is False
    assert object_storage.exists(moved.object_key) is True
    assert manager.list_artifacts(namespace="team-a") == []
    assert len(manager.list_artifacts(namespace="team-b")) == 1


def test_move_raises_for_missing_artifact(manager: ArtifactManager):
    with pytest.raises(KeyError):
        manager.move("missing", "team-b")


def test_delete_removes_artifact_and_payload(manager: ArtifactManager, object_storage: ObjectStorageEngine):
    artifact = manager.create("a.ipynb", ArtifactType.NOTEBOOK, b"data")

    removed = manager.delete(artifact.artifact_id)

    assert removed is True
    assert manager.fetch(artifact.artifact_id) is None
    assert object_storage.exists(artifact.object_key) is False


def test_delete_returns_false_for_missing_artifact(manager: ArtifactManager):
    assert manager.delete("missing") is False


def test_api_create_and_fetch_artifact(client: TestClient):
    create_response = client.post(
        "/storage/artifacts",
        params={"name": "model.pkl", "artifact_type": "model", "namespace": "default"},
        content=b"binary-data",
    )
    assert create_response.status_code == 200
    artifact_id = create_response.json()["artifact_id"]

    fetch_response = client.get(f"/storage/artifacts/{artifact_id}")
    assert fetch_response.status_code == 200
    assert fetch_response.json()["name"] == "model.pkl"


def test_api_fetch_missing_artifact_returns_404(client: TestClient):
    response = client.get("/storage/artifacts/missing")
    assert response.status_code == 404


def test_api_list_artifacts(client: TestClient):
    client.post(
        "/storage/artifacts",
        params={"name": "model.pkl", "artifact_type": "model"},
        content=b"data",
    )

    response = client.get("/storage/artifacts")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_delete_artifact(client: TestClient):
    create_response = client.post(
        "/storage/artifacts",
        params={"name": "model.pkl", "artifact_type": "model"},
        content=b"data",
    )
    artifact_id = create_response.json()["artifact_id"]

    delete_response = client.delete(f"/storage/artifacts/{artifact_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"artifact_id": artifact_id, "removed": True}

    missing_response = client.delete(f"/storage/artifacts/{artifact_id}")
    assert missing_response.status_code == 404
