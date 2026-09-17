from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.secret_vault import (
    InvalidSecretTypeError,
    SecretAlreadyExistsError,
    SecretEntry,
    SecretVaultService,
    UnknownSecretError,
    router as secret_vault_router,
)

BASE_TIME = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def vault() -> SecretVaultService:
    return SecretVaultService(encryption_key=b"0" * 32)


def test_store_returns_entry_with_plaintext_value(vault: SecretVaultService):
    entry = vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    assert isinstance(entry, SecretEntry)
    assert entry.value == "s3cr3t"
    assert entry.metadata.version == 1


def test_store_rejects_invalid_secret_type(vault: SecretVaultService):
    with pytest.raises(InvalidSecretTypeError):
        vault.store("x", "Database Passwords", "value")


def test_store_rejects_duplicate_name(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    with pytest.raises(SecretAlreadyExistsError):
        vault.store("db-password", "OAuth Credentials", "other", timestamp=BASE_TIME)


def test_secret_is_encrypted_at_rest(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    record = vault._secrets["db-password"]
    assert record["versions"][0]["ciphertext"] != b"s3cr3t"


def test_retrieve_returns_decrypted_value(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    entry = vault.retrieve("db-password", timestamp=BASE_TIME)

    assert entry.value == "s3cr3t"


def test_retrieve_unknown_secret_raises(vault: SecretVaultService):
    with pytest.raises(UnknownSecretError):
        vault.retrieve("does-not-exist")


def test_retrieve_tracks_access_count(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    vault.retrieve("db-password", timestamp=BASE_TIME)
    entry = vault.retrieve("db-password", timestamp=BASE_TIME)

    assert entry.metadata.access_count == 2


def test_access_log_records_store_and_retrieve(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)
    vault.retrieve("db-password", timestamp=BASE_TIME)

    log = vault.access_log("db-password")

    assert [entry["action"] for entry in log] == ["store", "retrieve"]


def test_rotate_creates_new_version(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    rotated = vault.rotate("db-password", "new-secret", timestamp=BASE_TIME)

    assert rotated.value == "new-secret"
    assert rotated.metadata.version == 2


def test_rotate_unknown_secret_raises(vault: SecretVaultService):
    with pytest.raises(UnknownSecretError):
        vault.rotate("does-not-exist", "new-secret")


def test_rotate_invokes_registered_hooks(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)
    seen = []
    vault.register_rotation_hook("db-password", lambda entry: seen.append(entry.value))

    vault.rotate("db-password", "new-secret", timestamp=BASE_TIME)

    assert seen == ["new-secret"]


def test_destroy_removes_secret(vault: SecretVaultService):
    vault.store("db-password", "OAuth Credentials", "s3cr3t", timestamp=BASE_TIME)

    vault.destroy("db-password", timestamp=BASE_TIME)

    with pytest.raises(UnknownSecretError):
        vault.retrieve("db-password")


def test_destroy_unknown_secret_raises(vault: SecretVaultService):
    with pytest.raises(UnknownSecretError):
        vault.destroy("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(secret_vault_router)
    return TestClient(app)


def test_api_store_secret(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)

    response = client.post(
        "/security/secrets",
        json={"name": "api-secret", "secret_type": "Encryption Keys", "value": "s3cr3t"},
    )

    assert response.status_code == 200
    assert response.json()["value"] == "s3cr3t"


def test_api_store_invalid_type_returns_422(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)

    response = client.post(
        "/security/secrets",
        json={"name": "api-secret", "secret_type": "Nope", "value": "s3cr3t"},
    )

    assert response.status_code == 422


def test_api_retrieve_secret(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)
    vault.store("api-retrieve", "Encryption Keys", "s3cr3t", timestamp=BASE_TIME)

    response = client.get("/security/secrets/api-retrieve")

    assert response.status_code == 200
    assert response.json()["value"] == "s3cr3t"


def test_api_retrieve_unknown_secret_returns_404(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)

    response = client.get("/security/secrets/does-not-exist")

    assert response.status_code == 404


def test_api_rotate_secret(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)
    vault.store("api-rotate", "Encryption Keys", "s3cr3t", timestamp=BASE_TIME)

    response = client.post("/security/secrets/api-rotate/rotate", json={"value": "new-secret"})

    assert response.status_code == 200
    assert response.json()["value"] == "new-secret"
    assert response.json()["version"] == 2


def test_api_destroy_secret(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)
    vault.store("api-destroy", "Encryption Keys", "s3cr3t", timestamp=BASE_TIME)

    response = client.delete("/security/secrets/api-destroy")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_api_destroy_unknown_secret_returns_404(client: TestClient, vault: SecretVaultService, monkeypatch):
    from backend.security import secret_vault as secret_vault_module

    monkeypatch.setattr(secret_vault_module, "_secret_vault_service", vault)

    response = client.delete("/security/secrets/does-not-exist")

    assert response.status_code == 404
