from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.rbac import RoleBasedAccessControl
from backend.security.permission_engine import PermissionEngine
from backend.security.secret_vault import SecretVaultService, UnknownSecretError as UnknownVaultSecretError
from backend.security.api_key_manager import (
    APIKey,
    APIKeyExpiredError,
    APIKeyManager,
    APIKeyMetadata,
    APIKeyRevokedError,
    InvalidKeyTypeError,
    UnknownAPIKeyError,
    router as api_key_manager_router,
)

BASE_TIME = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def rbac() -> RoleBasedAccessControl:
    return RoleBasedAccessControl()


@pytest.fixture
def permission_engine(rbac: RoleBasedAccessControl) -> PermissionEngine:
    return PermissionEngine(rbac=rbac)


@pytest.fixture
def secret_vault() -> SecretVaultService:
    return SecretVaultService(encryption_key=b"1" * 32)


@pytest.fixture
def manager(permission_engine: PermissionEngine, secret_vault: SecretVaultService) -> APIKeyManager:
    return APIKeyManager(permission_engine=permission_engine, secret_vault=secret_vault)


def test_create_key_returns_key_with_secret(manager: APIKeyManager):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    assert isinstance(api_key, APIKey)
    assert api_key.secret.startswith("ntbkv2_")
    assert api_key.metadata.key_type == "User"


def test_create_key_rejects_invalid_key_type(manager: APIKeyManager):
    with pytest.raises(InvalidKeyTypeError):
        manager.create_key("alice", "ci-key", "Superuser")


def test_create_key_grants_scopes_via_permission_engine(
    manager: APIKeyManager, permission_engine: PermissionEngine
):
    api_key = manager.create_key(
        "alice", "scoped-key", "Read-Only", scopes=["orders:Read"], timestamp=BASE_TIME
    )

    assert permission_engine.check(api_key.metadata.key_id, "orders", "Read") is True
    assert permission_engine.check(api_key.metadata.key_id, "orders", "Write") is False


def test_validate_key_returns_metadata(manager: APIKeyManager):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    metadata = manager.validate_key(api_key.secret, timestamp=BASE_TIME)

    assert isinstance(metadata, APIKeyMetadata)
    assert metadata.key_id == api_key.metadata.key_id


def test_validate_key_unknown_secret_raises(manager: APIKeyManager):
    with pytest.raises(UnknownAPIKeyError):
        manager.validate_key("not-a-real-key")


def test_validate_key_expired_raises(manager: APIKeyManager):
    api_key = manager.create_key(
        "alice", "temp-key", "Temporary", expires_at=BASE_TIME + timedelta(hours=1), timestamp=BASE_TIME
    )

    with pytest.raises(APIKeyExpiredError):
        manager.validate_key(api_key.secret, timestamp=BASE_TIME + timedelta(hours=2))


def test_validate_key_revoked_raises(manager: APIKeyManager):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)
    manager.revoke_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    with pytest.raises(APIKeyRevokedError):
        manager.validate_key(api_key.secret, timestamp=BASE_TIME)


def test_check_scope_true_for_granted_permission(manager: APIKeyManager):
    api_key = manager.create_key(
        "alice", "scoped-key", "Read-Only", scopes=["orders:Read"], timestamp=BASE_TIME
    )

    assert manager.check_scope(api_key.secret, "orders", "Read", timestamp=BASE_TIME) is True
    assert manager.check_scope(api_key.secret, "orders", "Write", timestamp=BASE_TIME) is False


def test_rotate_key_invalidates_old_secret(manager: APIKeyManager):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    rotated = manager.rotate_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    assert rotated.secret != api_key.secret
    with pytest.raises(UnknownAPIKeyError):
        manager.validate_key(api_key.secret, timestamp=BASE_TIME)


def test_rotate_key_preserves_identity_and_scopes(manager: APIKeyManager, permission_engine: PermissionEngine):
    api_key = manager.create_key(
        "alice", "scoped-key", "Service", scopes=["orders:Read"], timestamp=BASE_TIME
    )

    rotated = manager.rotate_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    assert rotated.metadata.identity == "alice"
    assert rotated.metadata.scopes == ("orders:Read",)
    assert rotated.metadata.rotated_from == api_key.metadata.key_id
    assert permission_engine.check(rotated.metadata.key_id, "orders", "Read") is True
    assert permission_engine.check(api_key.metadata.key_id, "orders", "Read") is False


def test_rotate_key_unknown_key_raises(manager: APIKeyManager):
    with pytest.raises(UnknownAPIKeyError):
        manager.rotate_key("does-not-exist")


def test_rotate_key_already_revoked_raises(manager: APIKeyManager):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)
    manager.revoke_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    with pytest.raises(APIKeyRevokedError):
        manager.rotate_key(api_key.metadata.key_id, timestamp=BASE_TIME)


def test_revoke_key_marks_metadata_revoked(manager: APIKeyManager):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    metadata = manager.revoke_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    assert metadata.revoked is True
    assert metadata.revoked_at == BASE_TIME


def test_revoke_key_unknown_raises(manager: APIKeyManager):
    with pytest.raises(UnknownAPIKeyError):
        manager.revoke_key("does-not-exist")


def test_revoke_key_clears_scoped_permissions(manager: APIKeyManager, permission_engine: PermissionEngine):
    api_key = manager.create_key(
        "alice", "scoped-key", "Service", scopes=["orders:Read"], timestamp=BASE_TIME
    )
    assert permission_engine.check(api_key.metadata.key_id, "orders", "Read") is True

    manager.revoke_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    assert permission_engine.check(api_key.metadata.key_id, "orders", "Read") is False


def test_create_key_stores_secret_in_vault(manager: APIKeyManager, secret_vault: SecretVaultService):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    entry = secret_vault.retrieve(f"api-key:{api_key.metadata.key_id}", timestamp=BASE_TIME)

    assert entry.value == api_key.secret


def test_revoke_key_destroys_vault_entry(manager: APIKeyManager, secret_vault: SecretVaultService):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    manager.revoke_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    with pytest.raises(UnknownVaultSecretError):
        secret_vault.retrieve(f"api-key:{api_key.metadata.key_id}")


def test_rotate_key_migrates_vault_entry(manager: APIKeyManager, secret_vault: SecretVaultService):
    api_key = manager.create_key("alice", "ci-key", "User", timestamp=BASE_TIME)

    rotated = manager.rotate_key(api_key.metadata.key_id, timestamp=BASE_TIME)

    with pytest.raises(UnknownVaultSecretError):
        secret_vault.retrieve(f"api-key:{api_key.metadata.key_id}")
    entry = secret_vault.retrieve(f"api-key:{rotated.metadata.key_id}", timestamp=BASE_TIME)
    assert entry.value == rotated.secret


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api_key_manager_router)
    return TestClient(app)


def test_api_create_key(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)

    response = client.post(
        "/security/api-keys", json={"identity": "api-alice", "name": "ci-key", "key_type": "User"}
    )

    assert response.status_code == 200
    assert response.json()["secret"].startswith("ntbkv2_")


def test_api_create_key_invalid_type_returns_422(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)

    response = client.post(
        "/security/api-keys", json={"identity": "api-alice", "name": "ci-key", "key_type": "Superuser"}
    )

    assert response.status_code == 422


def test_api_validate_key(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)
    api_key = manager.create_key("api-validate-alice", "ci-key", "User")

    response = client.post("/security/api-keys/validate", json={"secret": api_key.secret})

    assert response.status_code == 200
    assert response.json()["key_id"] == api_key.metadata.key_id


def test_api_validate_key_unknown_returns_404(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)

    response = client.post("/security/api-keys/validate", json={"secret": "not-a-real-key"})

    assert response.status_code == 404


def test_api_rotate_key(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)
    api_key = manager.create_key("api-rotate-alice", "ci-key", "User", timestamp=BASE_TIME)

    response = client.post(f"/security/api-keys/{api_key.metadata.key_id}/rotate")

    assert response.status_code == 200
    assert response.json()["secret"] != api_key.secret


def test_api_rotate_key_unknown_returns_404(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)

    response = client.post("/security/api-keys/does-not-exist/rotate")

    assert response.status_code == 404


def test_api_revoke_key(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)
    api_key = manager.create_key("api-revoke-alice", "ci-key", "User", timestamp=BASE_TIME)

    response = client.delete(f"/security/api-keys/{api_key.metadata.key_id}")

    assert response.status_code == 200
    assert response.json()["revoked"] is True


def test_api_revoke_key_unknown_returns_404(client: TestClient, manager: APIKeyManager, monkeypatch):
    from backend.security import api_key_manager as api_key_manager_module

    monkeypatch.setattr(api_key_manager_module, "_api_key_manager", manager)

    response = client.delete("/security/api-keys/does-not-exist")

    assert response.status_code == 404
