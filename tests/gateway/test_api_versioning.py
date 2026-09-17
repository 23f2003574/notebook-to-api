from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.api_versioning import (
    APIVersion,
    APIVersionManager,
    NoDefaultVersionError,
    UnknownVersionError,
    VersionAlreadyRegisteredError,
    VersionPolicy,
    get_version_manager,
    router as version_router,
)


@pytest.fixture
def manager() -> APIVersionManager:
    return APIVersionManager()


@pytest.fixture
def client(manager: APIVersionManager) -> TestClient:
    app = FastAPI()
    app.include_router(version_router)
    app.dependency_overrides[get_version_manager] = lambda: manager
    return TestClient(app)


def test_register_version_creates_entry(manager: APIVersionManager):
    entry = manager.register_version("v1")

    assert isinstance(entry, APIVersion)
    assert entry.version == "v1"
    assert entry.deprecated is False


def test_register_version_rejects_duplicate(manager: APIVersionManager):
    manager.register_version("v1")

    with pytest.raises(VersionAlreadyRegisteredError):
        manager.register_version("v1")


def test_register_version_requires_version_string(manager: APIVersionManager):
    with pytest.raises(ValueError):
        manager.register_version("")


def test_register_version_as_default_sets_default(manager: APIVersionManager):
    manager.register_version("v1", is_default=True)

    resolved = manager.resolve()

    assert resolved.version == "v1"


def test_registering_new_default_clears_previous_default(manager: APIVersionManager):
    manager.register_version("v1", is_default=True)
    manager.register_version("v2", is_default=True)

    versions = {entry.version: entry.is_default for entry in manager.supported_versions()}

    assert versions == {"v1": False, "v2": True}


def test_constructor_rejects_invalid_fallback_strategy():
    with pytest.raises(ValueError):
        APIVersionManager(policy=VersionPolicy(fallback_strategy="round-robin"))


# --- resolve ---


def test_resolve_without_default_raises(manager: APIVersionManager):
    manager.register_version("v1")

    with pytest.raises(NoDefaultVersionError):
        manager.resolve()


def test_resolve_returns_default_for_default_keyword(manager: APIVersionManager):
    manager.register_version("v1", is_default=True)

    assert manager.resolve("default").version == "v1"


def test_resolve_specific_version(manager: APIVersionManager):
    manager.register_version("v1")
    manager.register_version("v2")

    assert manager.resolve("v1").version == "v1"


def test_resolve_latest_returns_most_recently_registered(manager: APIVersionManager):
    manager.register_version("v1")
    manager.register_version("v2")

    assert manager.resolve("latest").version == "v2"


def test_resolve_unknown_version_raises(manager: APIVersionManager):
    with pytest.raises(UnknownVersionError):
        manager.resolve("v99")


# --- deprecate ---


def test_deprecate_marks_version_deprecated(manager: APIVersionManager):
    manager.register_version("v1")

    entry = manager.deprecate("v1")

    assert entry.deprecated is True
    assert entry.deprecated_at is not None


def test_deprecate_records_sunset_at(manager: APIVersionManager):
    manager.register_version("v1")
    sunset = datetime.now(timezone.utc) + timedelta(days=30)

    entry = manager.deprecate("v1", sunset_at=sunset, message="use v2 instead")

    assert entry.sunset_at == sunset
    assert entry.release_notes == "use v2 instead"


def test_deprecate_unknown_version_raises(manager: APIVersionManager):
    with pytest.raises(UnknownVersionError):
        manager.deprecate("v99")


def test_deprecated_version_still_resolves(manager: APIVersionManager):
    manager.register_version("v1")
    manager.deprecate("v1")

    entry = manager.resolve("v1")

    assert entry.deprecated is True


# --- supported_versions ---


def test_supported_versions_returns_all_by_default(manager: APIVersionManager):
    manager.register_version("v1")
    manager.register_version("v2")
    manager.deprecate("v1")

    versions = {entry.version for entry in manager.supported_versions()}

    assert versions == {"v1", "v2"}


def test_supported_versions_can_exclude_deprecated(manager: APIVersionManager):
    manager.register_version("v1")
    manager.register_version("v2")
    manager.deprecate("v1")

    versions = {entry.version for entry in manager.supported_versions(include_deprecated=False)}

    assert versions == {"v2"}


# --- compatibility ---


def test_check_compatibility_same_version_is_compatible(manager: APIVersionManager):
    manager.register_version("v1")

    assert manager.check_compatibility("v1", "v1") is True


def test_check_compatibility_true_when_declared(manager: APIVersionManager):
    manager.register_version("v1", compatible_with=("v2",))
    manager.register_version("v2")

    assert manager.check_compatibility("v1", "v2") is True


def test_check_compatibility_false_when_not_declared(manager: APIVersionManager):
    manager.register_version("v1")
    manager.register_version("v2")

    assert manager.check_compatibility("v1", "v2") is False


def test_check_compatibility_unknown_version_raises(manager: APIVersionManager):
    manager.register_version("v1")

    with pytest.raises(UnknownVersionError):
        manager.check_compatibility("v1", "v99")


# --- API ---


def test_api_register_and_list_version(client: TestClient):
    response = client.post("/gateway/versions", json={"version": "v1", "is_default": True})
    assert response.status_code == 201
    assert response.json()["version"] == "v1"

    listed = client.get("/gateway/versions")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/gateway/versions", json={"version": "v1"})
    response = client.post("/gateway/versions", json={"version": "v1"})

    assert response.status_code == 409


def test_api_register_missing_version_returns_422(client: TestClient):
    response = client.post("/gateway/versions", json={})

    assert response.status_code == 422


def test_api_resolve_specific_version(client: TestClient):
    client.post("/gateway/versions", json={"version": "v1"})

    response = client.get("/gateway/versions/v1")

    assert response.status_code == 200
    assert response.json()["version"] == "v1"


def test_api_resolve_unknown_version_returns_404(client: TestClient):
    response = client.get("/gateway/versions/v99")

    assert response.status_code == 404


def test_api_resolve_latest(client: TestClient):
    client.post("/gateway/versions", json={"version": "v1"})
    client.post("/gateway/versions", json={"version": "v2"})

    response = client.get("/gateway/versions/latest")

    assert response.status_code == 200
    assert response.json()["version"] == "v2"


def test_api_deprecate_version(client: TestClient):
    client.post("/gateway/versions", json={"version": "v1"})

    response = client.post("/gateway/versions/v1/deprecate", json={"message": "use v2"})

    assert response.status_code == 200
    body = response.json()
    assert body["deprecated"] is True
    assert body["release_notes"] == "use v2"


def test_api_deprecate_unknown_version_returns_404(client: TestClient):
    response = client.post("/gateway/versions/v99/deprecate", json={})

    assert response.status_code == 404


def test_api_deprecate_invalid_sunset_at_returns_422(client: TestClient):
    client.post("/gateway/versions", json={"version": "v1"})

    response = client.post("/gateway/versions/v1/deprecate", json={"sunset_at": "not-a-date"})

    assert response.status_code == 422
