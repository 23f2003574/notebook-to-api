import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.object_storage import (
    ObjectMetadata,
    ObjectStorageBackend,
    ObjectStorageEngine,
    StorageObject,
    get_object_storage_engine,
    router as object_storage_router,
)
from backend.storage.storage_registry import StorageMetadata, StorageRegistry


@pytest.fixture
def registry() -> StorageRegistry:
    return StorageRegistry()


@pytest.fixture
def engine(registry: StorageRegistry) -> ObjectStorageEngine:
    return ObjectStorageEngine(registry=registry)


@pytest.fixture
def client(engine: ObjectStorageEngine) -> TestClient:
    app = FastAPI()
    app.include_router(object_storage_router)
    app.dependency_overrides[get_object_storage_engine] = lambda: engine
    return TestClient(app)


def test_put_creates_object(engine: ObjectStorageEngine):
    obj = engine.put("artifacts/report.json", b"{}", content_type="application/json")

    assert isinstance(obj, StorageObject)
    assert obj.key == "artifacts/report.json"
    assert obj.backend == ObjectStorageBackend.LOCAL
    assert isinstance(obj.metadata, ObjectMetadata)
    assert obj.metadata.size == 2


def test_put_rejects_empty_key(engine: ObjectStorageEngine):
    with pytest.raises(ValueError):
        engine.put("", b"data")


def test_put_accepts_chunked_iterable(engine: ObjectStorageEngine):
    chunks = [b"hello ", b"world"]

    obj = engine.put("greeting.txt", iter(chunks))

    assert obj.data == b"hello world"
    assert obj.metadata.size == 11


def test_get_returns_stored_object(engine: ObjectStorageEngine):
    engine.put("a.txt", b"hello")

    obj = engine.get("a.txt")

    assert obj is not None
    assert obj.data == b"hello"


def test_get_returns_none_for_missing_object(engine: ObjectStorageEngine):
    assert engine.get("missing") is None


def test_get_stream_yields_chunks(engine: ObjectStorageEngine):
    engine.put("a.txt", b"hello world")

    chunks = list(engine.get_stream("a.txt", chunk_size=4))

    assert b"".join(chunks) == b"hello world"
    assert all(len(chunk) <= 4 for chunk in chunks)


def test_get_stream_raises_for_missing_object(engine: ObjectStorageEngine):
    with pytest.raises(KeyError):
        list(engine.get_stream("missing"))


def test_delete_removes_object(engine: ObjectStorageEngine):
    engine.put("a.txt", b"hello")

    removed = engine.delete("a.txt")

    assert removed is True
    assert engine.exists("a.txt") is False


def test_delete_returns_false_for_missing_object(engine: ObjectStorageEngine):
    assert engine.delete("missing") is False


def test_exists_reflects_current_state(engine: ObjectStorageEngine):
    assert engine.exists("a.txt") is False
    engine.put("a.txt", b"hello")
    assert engine.exists("a.txt") is True


def test_put_validates_backend_id_when_registry_present(engine: ObjectStorageEngine):
    with pytest.raises(ValueError):
        engine.put("a.txt", b"hello", backend_id="unregistered-backend")


def test_put_succeeds_with_active_backend_id(engine: ObjectStorageEngine, registry: StorageRegistry):
    registry.register(
        "backend-1",
        ["read", "write"],
        StorageMetadata(kind="s3", region="us-east-1", version="1.0.0"),
    )

    obj = engine.put("a.txt", b"hello", backend_id="backend-1")

    assert obj.data == b"hello"


def test_api_put_and_get_object(client: TestClient):
    put_response = client.put("/storage/objects/a.txt", content=b"hello")
    assert put_response.status_code == 200
    assert put_response.json()["key"] == "a.txt"

    get_response = client.get("/storage/objects/a.txt")
    assert get_response.status_code == 200
    assert get_response.content == b"hello"


def test_api_get_missing_object_returns_404(client: TestClient):
    response = client.get("/storage/objects/missing.txt")
    assert response.status_code == 404


def test_api_head_object(client: TestClient):
    client.put("/storage/objects/a.txt", content=b"hello")

    response = client.head("/storage/objects/a.txt")

    assert response.status_code == 200
    assert response.headers["Content-Length"] == "5"


def test_api_delete_object(client: TestClient):
    client.put("/storage/objects/a.txt", content=b"hello")

    delete_response = client.delete("/storage/objects/a.txt")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"key": "a.txt", "removed": True}

    missing_response = client.delete("/storage/objects/a.txt")
    assert missing_response.status_code == 404
