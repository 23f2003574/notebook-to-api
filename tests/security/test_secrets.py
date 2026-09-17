from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.authentication import AuthenticationManager
from backend.security.rbac import RoleBasedAccessControl
from backend.security.permissions import PermissionEngine
from backend.security.security_policy import SecurityPolicyEngine
from backend.security.secrets import (
    AccessDeniedError,
    InvalidSecretTypeError,
    Secret,
    SecretAlreadyExistsError,
    SecretExpiredError,
    SecretManagementService,
    UnknownSecretError,
    router as secrets_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def authentication_manager() -> AuthenticationManager:
    return AuthenticationManager()


@pytest.fixture
def rbac(authentication_manager: AuthenticationManager) -> RoleBasedAccessControl:
    return RoleBasedAccessControl(authentication_manager=authentication_manager)


@pytest.fixture
def permission_engine(rbac: RoleBasedAccessControl) -> PermissionEngine:
    return PermissionEngine(rbac=rbac)


@pytest.fixture
def security_policy_engine(permission_engine: PermissionEngine) -> SecurityPolicyEngine:
    return SecurityPolicyEngine(permission_engine=permission_engine)


@pytest.fixture
def user_id(authentication_manager: AuthenticationManager) -> str:
    return authentication_manager.register("alice", "hunter2", timestamp=BASE_TIME).user_id


@pytest.fixture
def service(
    permission_engine: PermissionEngine, security_policy_engine: SecurityPolicyEngine
) -> SecretManagementService:
    return SecretManagementService(
        permission_engine=permission_engine,
        security_policy_engine=security_policy_engine,
        encryption_key=b"0" * 32,
    )


def test_store_returns_secret_with_plaintext_value(service: SecretManagementService):
    secret = service.store("db-password", "Database Credentials", "s3cr3t-value", timestamp=BASE_TIME)

    assert isinstance(secret, Secret)
    assert secret.value == "s3cr3t-value"
    assert secret.metadata.version == 1


def test_store_rejects_unknown_secret_type(service: SecretManagementService):
    with pytest.raises(InvalidSecretTypeError):
        service.store("db-password", "Not A Real Type", "value")


def test_store_duplicate_name_raises(service: SecretManagementService):
    service.store("db-password", "Database Credentials", "value")

    with pytest.raises(SecretAlreadyExistsError):
        service.store("db-password", "Database Credentials", "other-value")


def test_stored_value_is_encrypted_at_rest(service: SecretManagementService):
    service.store("db-password", "Database Credentials", "s3cr3t-value", timestamp=BASE_TIME)

    stored = service._secrets["db-password"]["versions"][0]["ciphertext"]
    assert b"s3cr3t-value" not in stored


def test_retrieve_returns_decrypted_value(service: SecretManagementService):
    service.store("db-password", "Database Credentials", "s3cr3t-value", timestamp=BASE_TIME)

    secret = service.retrieve("db-password", timestamp=BASE_TIME)

    assert secret.value == "s3cr3t-value"


def test_retrieve_unknown_secret_raises(service: SecretManagementService):
    with pytest.raises(UnknownSecretError):
        service.retrieve("does-not-exist")


def test_retrieve_expired_secret_raises(service: SecretManagementService):
    service.store(
        "db-password",
        "Database Credentials",
        "value",
        expires_at=BASE_TIME + timedelta(hours=1),
        timestamp=BASE_TIME,
    )

    with pytest.raises(SecretExpiredError):
        service.retrieve("db-password", timestamp=BASE_TIME + timedelta(hours=2))


def test_retrieve_enforces_access_control(
    service: SecretManagementService, rbac: RoleBasedAccessControl, user_id: str
):
    service.store("db-password", "Database Credentials", "value", timestamp=BASE_TIME)
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)

    with pytest.raises(AccessDeniedError):
        service.retrieve("db-password", user_id=user_id)


def test_retrieve_allows_user_with_permission(
    service: SecretManagementService,
    rbac: RoleBasedAccessControl,
    permission_engine: PermissionEngine,
    user_id: str,
):
    service.store("db-password", "Database Credentials", "value", timestamp=BASE_TIME)
    rbac.assign_role(user_id, "Viewer", timestamp=BASE_TIME)
    permission_engine.grant("Viewer", "secret:db-password", "Read", timestamp=BASE_TIME)

    secret = service.retrieve("db-password", user_id=user_id)

    assert secret.value == "value"


def test_rotate_creates_new_version(service: SecretManagementService):
    service.store("db-password", "Database Credentials", "old-value", timestamp=BASE_TIME)

    rotated = service.rotate("db-password", "new-value", timestamp=BASE_TIME + timedelta(days=1))

    assert rotated.value == "new-value"
    assert rotated.metadata.version == 2
    assert service.retrieve("db-password", timestamp=BASE_TIME + timedelta(days=1)).value == "new-value"


def test_rotate_unknown_secret_raises(service: SecretManagementService):
    with pytest.raises(UnknownSecretError):
        service.rotate("does-not-exist", "value")


def test_rotate_reports_compliance_when_policy_registered(
    service: SecretManagementService, security_policy_engine: SecurityPolicyEngine
):
    security_policy_engine.register_policy("API Key Rotation", config={"max_age_days": 90})
    service.store("api-key", "API Keys", "old-value", timestamp=BASE_TIME)

    compliant = service.rotate("api-key", "new-value", timestamp=BASE_TIME + timedelta(days=10))
    noncompliant = service.rotate(
        "api-key", "another-value", timestamp=BASE_TIME + timedelta(days=10, hours=1) + timedelta(days=200)
    )

    assert compliant.metadata.rotation_compliant is True
    assert noncompliant.metadata.rotation_compliant is False


def test_rotate_compliance_is_none_when_policy_not_registered(service: SecretManagementService):
    service.store("api-key", "API Keys", "old-value", timestamp=BASE_TIME)

    rotated = service.rotate("api-key", "new-value", timestamp=BASE_TIME + timedelta(days=1))

    assert rotated.metadata.rotation_compliant is None


def test_delete_removes_secret(service: SecretManagementService):
    service.store("db-password", "Database Credentials", "value", timestamp=BASE_TIME)

    service.delete("db-password")

    with pytest.raises(UnknownSecretError):
        service.retrieve("db-password")


def test_delete_unknown_secret_raises(service: SecretManagementService):
    with pytest.raises(UnknownSecretError):
        service.delete("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(secrets_router)
    return TestClient(app)


def test_api_store_secret(client: TestClient):
    response = client.post(
        "/security/secrets",
        json={"name": "api-store-secret", "secret_type": "OAuth Secrets", "value": "s3cr3t"},
    )

    assert response.status_code == 200
    assert response.json()["value"] == "s3cr3t"


def test_api_store_secret_invalid_type_returns_422(client: TestClient):
    response = client.post(
        "/security/secrets",
        json={"name": "api-invalid-type", "secret_type": "Not A Real Type", "value": "s3cr3t"},
    )

    assert response.status_code == 422


def test_api_store_secret_duplicate_returns_409(client: TestClient):
    client.post(
        "/security/secrets",
        json={"name": "api-dup-secret", "secret_type": "OAuth Secrets", "value": "s3cr3t"},
    )

    response = client.post(
        "/security/secrets",
        json={"name": "api-dup-secret", "secret_type": "OAuth Secrets", "value": "other"},
    )

    assert response.status_code == 409


def test_api_retrieve_secret(client: TestClient):
    client.post(
        "/security/secrets",
        json={"name": "api-retrieve-secret", "secret_type": "Encryption Keys", "value": "s3cr3t"},
    )

    response = client.get("/security/secrets/api-retrieve-secret")

    assert response.status_code == 200
    assert response.json()["value"] == "s3cr3t"


def test_api_retrieve_unknown_secret_returns_404(client: TestClient):
    response = client.get("/security/secrets/does-not-exist")

    assert response.status_code == 404


def test_api_rotate_secret(client: TestClient):
    client.post(
        "/security/secrets",
        json={"name": "api-rotate-secret", "secret_type": "JWT Signing Keys", "value": "old"},
    )

    response = client.post("/security/secrets/api-rotate-secret/rotate", json={"value": "new"})

    assert response.status_code == 200
    assert response.json()["value"] == "new"
    assert response.json()["version"] == 2


def test_api_rotate_unknown_secret_returns_404(client: TestClient):
    response = client.post("/security/secrets/does-not-exist/rotate", json={"value": "new"})

    assert response.status_code == 404


def test_api_delete_secret(client: TestClient):
    client.post(
        "/security/secrets",
        json={"name": "api-delete-secret", "secret_type": "Encryption Keys", "value": "s3cr3t"},
    )

    response = client.delete("/security/secrets/api-delete-secret")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_delete_unknown_secret_returns_404(client: TestClient):
    response = client.delete("/security/secrets/does-not-exist")

    assert response.status_code == 404
