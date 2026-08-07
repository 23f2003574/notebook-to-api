from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.identity_registry import (
    Identity,
    IdentityAlreadyExistsError,
    IdentityRegistry,
    UnknownIdentityError,
    router as identity_registry_router,
)

BASE_TIME = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def registry() -> IdentityRegistry:
    return IdentityRegistry()


def test_register_identity_returns_identity(registry: IdentityRegistry):
    identity = registry.register_identity(
        "alice", "user", attributes={"team": "platform"}, timestamp=BASE_TIME
    )

    assert isinstance(identity, Identity)
    assert identity.metadata.display_name == "alice"
    assert identity.metadata.identity_type == "user"
    assert identity.metadata.attributes == {"team": "platform"}


def test_register_identity_rejects_duplicate_display_name(registry: IdentityRegistry):
    registry.register_identity("alice", "user", timestamp=BASE_TIME)

    with pytest.raises(IdentityAlreadyExistsError):
        registry.register_identity("alice", "user", timestamp=BASE_TIME)


def test_lookup_returns_registered_identity(registry: IdentityRegistry):
    registered = registry.register_identity("bob", "service", timestamp=BASE_TIME)

    found = registry.lookup(registered.identity_id)

    assert found.identity_id == registered.identity_id
    assert found.metadata.display_name == "bob"


def test_lookup_unknown_identity_raises(registry: IdentityRegistry):
    with pytest.raises(UnknownIdentityError):
        registry.lookup("does-not-exist")


def test_list_identities_returns_all_registered(registry: IdentityRegistry):
    registry.register_identity("alice", "user", timestamp=BASE_TIME)
    registry.register_identity("bob", "service", timestamp=BASE_TIME)

    identities = registry.list_identities()

    assert {identity.metadata.display_name for identity in identities} == {"alice", "bob"}


def test_update_merges_attributes(registry: IdentityRegistry):
    registered = registry.register_identity(
        "alice", "user", attributes={"team": "platform"}, timestamp=BASE_TIME
    )

    updated = registry.update(
        registered.identity_id, attributes={"role": "admin"}, timestamp=BASE_TIME
    )

    assert updated.metadata.attributes == {"team": "platform", "role": "admin"}
    assert updated.metadata.updated_at == BASE_TIME


def test_update_unknown_identity_raises(registry: IdentityRegistry):
    with pytest.raises(UnknownIdentityError):
        registry.update("does-not-exist", attributes={"role": "admin"})


def test_remove_deletes_identity(registry: IdentityRegistry):
    registered = registry.register_identity("alice", "user", timestamp=BASE_TIME)

    registry.remove(registered.identity_id)

    with pytest.raises(UnknownIdentityError):
        registry.lookup(registered.identity_id)


def test_remove_frees_display_name_for_reuse(registry: IdentityRegistry):
    registered = registry.register_identity("alice", "user", timestamp=BASE_TIME)
    registry.remove(registered.identity_id)

    reregistered = registry.register_identity("alice", "user", timestamp=BASE_TIME)

    assert reregistered.metadata.display_name == "alice"


def test_remove_unknown_identity_raises(registry: IdentityRegistry):
    with pytest.raises(UnknownIdentityError):
        registry.remove("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(identity_registry_router)
    return TestClient(app)


def test_api_register_identity(client: TestClient):
    response = client.post(
        "/security/identities", json={"display_name": "api-alice", "identity_type": "user"}
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "api-alice"


def test_api_register_identity_duplicate_returns_409(client: TestClient):
    client.post(
        "/security/identities", json={"display_name": "api-duplicate", "identity_type": "user"}
    )

    response = client.post(
        "/security/identities", json={"display_name": "api-duplicate", "identity_type": "user"}
    )

    assert response.status_code == 409


def test_api_list_identities(client: TestClient):
    before = client.get("/security/identities").json()
    client.post(
        "/security/identities", json={"display_name": "api-list-alice", "identity_type": "user"}
    )
    client.post(
        "/security/identities",
        json={"display_name": "api-list-bob", "identity_type": "service"},
    )

    response = client.get("/security/identities")

    assert response.status_code == 200
    assert len(response.json()) == len(before) + 2


def test_api_lookup_identity(client: TestClient):
    create_response = client.post(
        "/security/identities", json={"display_name": "api-lookup-alice", "identity_type": "user"}
    )
    identity_id = create_response.json()["identity_id"]

    response = client.get(f"/security/identities/{identity_id}")

    assert response.status_code == 200
    assert response.json()["display_name"] == "api-lookup-alice"


def test_api_lookup_unknown_identity_returns_404(client: TestClient):
    response = client.get("/security/identities/does-not-exist")

    assert response.status_code == 404


def test_api_remove_identity(client: TestClient):
    create_response = client.post(
        "/security/identities", json={"display_name": "api-remove-alice", "identity_type": "user"}
    )
    identity_id = create_response.json()["identity_id"]

    response = client.delete(f"/security/identities/{identity_id}")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_remove_unknown_identity_returns_404(client: TestClient):
    response = client.delete("/security/identities/does-not-exist")

    assert response.status_code == 404
