from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager, UnknownUserError
from backend.security.api_keys import (
    APIKey,
    APIKeyExpiredError,
    APIKeyManager,
    APIKeyMetadata,
    APIKeyRevokedError,
    UnknownAPIKeyError,
    router as api_keys_router,
)
from backend.security.authentication import router as authentication_router

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def manager(authentication_manager: AuthenticationManager) -> APIKeyManager:
    return APIKeyManager(authentication_manager=authentication_manager)


def test_create_generates_key_with_secret(manager: APIKeyManager, user_id: str):
    api_key = manager.create(user_id, "ci-key", timestamp=BASE_TIME)

    assert isinstance(api_key, APIKey)
    assert api_key.secret.startswith("ntbk_")
    assert api_key.metadata.user_id == user_id
    assert api_key.metadata.revoked is False


def test_create_requires_known_user(manager: APIKeyManager):
    with pytest.raises(UnknownUserError):
        manager.create("does-not-exist", "ci-key")


def test_validate_accepts_active_key(manager: APIKeyManager, user_id: str):
    api_key = manager.create(user_id, "ci-key", timestamp=BASE_TIME)

    metadata = manager.validate(api_key.secret, timestamp=BASE_TIME)

    assert isinstance(metadata, APIKeyMetadata)
    assert metadata.key_id == api_key.metadata.key_id


def test_validate_rejects_unknown_secret(manager: APIKeyManager):
    with pytest.raises(UnknownAPIKeyError):
        manager.validate("ntbk_does-not-exist")


def test_validate_rejects_expired_key(manager: APIKeyManager, user_id: str):
    api_key = manager.create(
        user_id, "ci-key", expires_at=BASE_TIME + timedelta(hours=1), timestamp=BASE_TIME
    )

    with pytest.raises(APIKeyExpiredError):
        manager.validate(api_key.secret, timestamp=BASE_TIME + timedelta(hours=2))


def test_validate_accepts_key_before_expiration(manager: APIKeyManager, user_id: str):
    api_key = manager.create(
        user_id, "ci-key", expires_at=BASE_TIME + timedelta(hours=1), timestamp=BASE_TIME
    )

    metadata = manager.validate(api_key.secret, timestamp=BASE_TIME + timedelta(minutes=30))

    assert metadata.key_id == api_key.metadata.key_id


def test_revoke_marks_key_revoked(manager: APIKeyManager, user_id: str):
    api_key = manager.create(user_id, "ci-key", timestamp=BASE_TIME)

    metadata = manager.revoke(api_key.metadata.key_id, timestamp=BASE_TIME)

    assert metadata.revoked is True
    assert metadata.revoked_at == BASE_TIME


def test_validate_rejects_revoked_key(manager: APIKeyManager, user_id: str):
    api_key = manager.create(user_id, "ci-key", timestamp=BASE_TIME)
    manager.revoke(api_key.metadata.key_id, timestamp=BASE_TIME)

    with pytest.raises(APIKeyRevokedError):
        manager.validate(api_key.secret)


def test_revoke_unknown_key_raises(manager: APIKeyManager):
    with pytest.raises(UnknownAPIKeyError):
        manager.revoke("does-not-exist")


def test_list_keys_returns_all(manager: APIKeyManager, user_id: str):
    manager.create(user_id, "key-a", timestamp=BASE_TIME)
    manager.create(user_id, "key-b", timestamp=BASE_TIME)

    assert len(manager.list_keys()) == 2


def test_list_keys_filters_by_user(manager: APIKeyManager, authentication_manager: AuthenticationManager, user_id: str):
    other_user_id = authentication_manager.register("bob", "s3cret", timestamp=BASE_TIME).user_id
    manager.create(user_id, "key-a", timestamp=BASE_TIME)
    manager.create(other_user_id, "key-b", timestamp=BASE_TIME)

    keys = manager.list_keys(user_id=user_id)

    assert len(keys) == 1
    assert keys[0].user_id == user_id


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(authentication_router)
    app.include_router(api_keys_router)
    return TestClient(app)


def _register_user(client: TestClient, username: str) -> str:
    response = client.post(
        "/security/register", json={"username": username, "password": "hunter2"}
    )
    return response.json()["user_id"]


def test_api_create_key(client: TestClient):
    user_id = _register_user(client, "api-key-create")

    response = client.post(
        "/security/api-keys", json={"user_id": user_id, "name": "ci-key"}
    )

    assert response.status_code == 200
    assert response.json()["secret"].startswith("ntbk_")


def test_api_create_key_unknown_user_returns_404(client: TestClient):
    response = client.post(
        "/security/api-keys", json={"user_id": "does-not-exist", "name": "ci-key"}
    )

    assert response.status_code == 404


def test_api_list_keys(client: TestClient):
    user_id = _register_user(client, "api-key-list")
    client.post("/security/api-keys", json={"user_id": user_id, "name": "key-a"})
    client.post("/security/api-keys", json={"user_id": user_id, "name": "key-b"})

    response = client.get("/security/api-keys", params={"user_id": user_id})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_list_keys_filters_by_user(client: TestClient):
    user_id = _register_user(client, "api-key-filter-a")
    other_user_id = _register_user(client, "api-key-filter-b")
    client.post("/security/api-keys", json={"user_id": user_id, "name": "key-a"})
    client.post("/security/api-keys", json={"user_id": other_user_id, "name": "key-b"})

    response = client.get("/security/api-keys", params={"user_id": user_id})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_revoke_key(client: TestClient):
    user_id = _register_user(client, "api-key-revoke")
    create_response = client.post(
        "/security/api-keys", json={"user_id": user_id, "name": "ci-key"}
    )
    key_id = create_response.json()["key_id"]

    response = client.delete(f"/security/api-keys/{key_id}")

    assert response.status_code == 200
    assert response.json()["revoked"] is True


def test_api_revoke_unknown_key_returns_404(client: TestClient):
    response = client.delete("/security/api-keys/does-not-exist")

    assert response.status_code == 404
