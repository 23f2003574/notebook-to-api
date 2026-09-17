import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.storage_registry import (
    StorageBackend,
    StorageMetadata,
    StorageRegistry,
    get_storage_registry,
    router as storage_registry_router,
)


def make_metadata(kind: str = "s3") -> StorageMetadata:
    return StorageMetadata(kind=kind, region="us-east-1", version="1.0.0", tags=("durable",))


@pytest.fixture
def registry() -> StorageRegistry:
    return StorageRegistry()


@pytest.fixture
def client(registry: StorageRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(storage_registry_router)
    app.dependency_overrides[get_storage_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_backend(registry: StorageRegistry):
    backend = registry.register("backend-1", ["read", "write"], make_metadata())

    assert isinstance(backend, StorageBackend)
    assert backend.backend_id == "backend-1"
    assert backend.capabilities == ("read", "write")
    assert backend.status == "active"


def test_register_rejects_empty_backend_id(registry: StorageRegistry):
    with pytest.raises(ValueError):
        registry.register("", ["read"], make_metadata())


def test_register_rejects_invalid_status(registry: StorageRegistry):
    with pytest.raises(ValueError):
        registry.register("backend-1", ["read"], make_metadata(), status="hibernating")


def test_register_rejects_duplicate_backend(registry: StorageRegistry):
    registry.register("backend-1", ["read"], make_metadata())
    with pytest.raises(ValueError):
        registry.register("backend-1", ["write"], make_metadata())


def test_get_returns_registered_backend(registry: StorageRegistry):
    registry.register("backend-1", ["read"], make_metadata())

    backend = registry.get("backend-1")

    assert backend is not None
    assert backend.backend_id == "backend-1"


def test_get_returns_none_for_missing_backend(registry: StorageRegistry):
    assert registry.get("missing") is None


def test_remove_deletes_backend(registry: StorageRegistry):
    registry.register("backend-1", ["read"], make_metadata())

    removed = registry.remove("backend-1")

    assert removed is True
    assert registry.get("backend-1") is None


def test_remove_returns_false_for_missing_backend(registry: StorageRegistry):
    assert registry.remove("missing") is False


def test_list_backends_filters_by_status(registry: StorageRegistry):
    registry.register("backend-1", ["read"], make_metadata(), status="active")
    registry.register("backend-2", ["read"], make_metadata(), status="offline")

    active = registry.list_backends(status="active")

    assert [backend.backend_id for backend in active] == ["backend-1"]


def test_list_backends_filters_by_capability(registry: StorageRegistry):
    registry.register("backend-1", ["read"], make_metadata())
    registry.register("backend-2", ["write"], make_metadata())

    readers = registry.list_backends(capability="read")

    assert [backend.backend_id for backend in readers] == ["backend-1"]


def test_remove_unindexes_capabilities(registry: StorageRegistry):
    registry.register("backend-1", ["read"], make_metadata())
    registry.remove("backend-1")

    assert registry.list_backends(capability="read") == []


def test_api_register_and_get_backend(client: TestClient):
    payload = {
        "backend_id": "backend-1",
        "capabilities": ["read", "write"],
        "metadata": {"kind": "s3", "region": "us-east-1", "version": "1.0.0", "tags": ["durable"]},
    }

    create_response = client.post("/storage/backends", json=payload)
    assert create_response.status_code == 200
    assert create_response.json()["backend_id"] == "backend-1"

    get_response = client.get("/storage/backends/backend-1")
    assert get_response.status_code == 200
    assert get_response.json()["backend_id"] == "backend-1"


def test_api_get_missing_backend_returns_404(client: TestClient):
    response = client.get("/storage/backends/missing")
    assert response.status_code == 404


def test_api_list_backends(client: TestClient):
    payload = {
        "backend_id": "backend-1",
        "capabilities": ["read"],
        "metadata": {"kind": "s3", "region": "us-east-1", "version": "1.0.0"},
    }
    client.post("/storage/backends", json=payload)

    response = client.get("/storage/backends")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_delete_backend(client: TestClient):
    payload = {
        "backend_id": "backend-1",
        "capabilities": ["read"],
        "metadata": {"kind": "s3", "region": "us-east-1", "version": "1.0.0"},
    }
    client.post("/storage/backends", json=payload)

    delete_response = client.delete("/storage/backends/backend-1")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"backend_id": "backend-1", "removed": True}

    missing_response = client.delete("/storage/backends/backend-1")
    assert missing_response.status_code == 404


def test_api_register_duplicate_returns_422(client: TestClient):
    payload = {
        "backend_id": "backend-1",
        "capabilities": ["read"],
        "metadata": {"kind": "s3", "region": "us-east-1", "version": "1.0.0"},
    }
    client.post("/storage/backends", json=payload)

    duplicate_response = client.post("/storage/backends", json=payload)

    assert duplicate_response.status_code == 422
