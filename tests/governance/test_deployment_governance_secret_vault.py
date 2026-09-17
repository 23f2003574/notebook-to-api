from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_secret_vault import (
    BUILT_IN_SECRET_VAULT_PROVIDERS,
    DeploymentSecretVault,
    EnvironmentVariablesProvider,
    InMemoryVaultProvider,
    SecretMetadata,
    SecretReference,
    get_secret_vault,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _vault(**kwargs) -> DeploymentSecretVault:
    return DeploymentSecretVault(clock=_clock, environment={}, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The secret vault is a process-wide singleton; most tests below
    construct their own fresh vault instead (see _vault), and only the
    singleton and API tests touch the shared instance, matching
    test_deployment_governance_authentication.py's own fixture.
    """

    def _reset():
        get_secret_vault().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSecretReference:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            SecretReference(name="", version="1", provider="InMemoryVault")

    def test_rejects_empty_version(self):
        with pytest.raises(
            ValueError, match="version must not be empty"
        ):
            SecretReference(name="s", version="", provider="InMemoryVault")

    def test_rejects_empty_provider(self):
        with pytest.raises(
            ValueError, match="provider must not be empty"
        ):
            SecretReference(name="s", version="1", provider="")

    def test_to_dict(self):
        reference = SecretReference(
            name="s", version="1", provider="InMemoryVault"
        )

        assert reference.to_dict() == {
            "name": "s", "version": "1", "provider": "InMemoryVault",
        }

    def test_has_no_value_field(self):
        reference = SecretReference(
            name="s", version="1", provider="InMemoryVault"
        )

        assert not hasattr(reference, "value")


class TestSecretMetadata:

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            SecretMetadata(
                created_at=datetime(2026, 7, 24, 12, 0, 0),
                expires_at=None, rotated=False,
            )

    def test_rejects_naive_expires_at(self):
        with pytest.raises(
            ValueError, match="expires_at must be timezone-aware"
        ):
            SecretMetadata(
                created_at=BASE_TIME,
                expires_at=datetime(2026, 7, 24, 12, 0, 0),
                rotated=False,
            )

    def test_to_dict(self):
        metadata = SecretMetadata(
            created_at=BASE_TIME, expires_at=None, rotated=False
        )

        assert metadata.to_dict() == {
            "created_at": BASE_TIME.isoformat(),
            "expires_at": None,
            "rotated": False,
        }

    def test_has_no_value_field(self):
        metadata = SecretMetadata(
            created_at=BASE_TIME, expires_at=None, rotated=False
        )

        assert not hasattr(metadata, "value")


# --- Store / fetch -----------------------------------------------------


class TestStoreFetch:

    def test_store_returns_reference(self):
        vault = _vault()

        reference = vault.store("s1", "top-secret")

        assert reference.name == "s1"
        assert reference.version == "1"
        assert reference.provider == "InMemoryVault"

    def test_fetch_returns_stored_value(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        assert vault.fetch("s1") == "top-secret"

    def test_fetch_unknown_raises(self):
        vault = _vault()

        with pytest.raises(KeyError):
            vault.fetch("does-not-exist")

    def test_exists(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        assert vault.exists("s1") is True
        assert vault.exists("does-not-exist") is False

    def test_publishes_secret_stored_without_value(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("secret_stored", events.append)
        vault = _vault(event_bus=bus)

        vault.store("s1", "top-secret")

        assert len(events) == 1
        assert "top-secret" not in str(events[0].payload)
        assert "value" not in events[0].payload

    def test_publishes_secret_retrieved_without_value(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("secret_retrieved", events.append)
        vault = _vault(event_bus=bus)
        vault.store("s1", "top-secret")

        vault.fetch("s1")

        assert len(events) == 1
        assert "top-secret" not in str(events[0].payload)


# --- Duplicate handling -------------------------------------------------


class TestDuplicateHandling:

    def test_store_duplicate_name_raises(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        with pytest.raises(ValueError, match="already stored"):
            vault.store("s1", "another-secret")

    def test_original_value_survives_failed_duplicate_store(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        with pytest.raises(ValueError):
            vault.store("s1", "another-secret")

        assert vault.fetch("s1") == "top-secret"


# --- Rotation ------------------------------------------------------------


class TestRotation:

    def test_rotate_replaces_value(self):
        vault = _vault()
        vault.store("s1", "old-secret")

        vault.rotate("s1", "new-secret")

        assert vault.fetch("s1") == "new-secret"

    def test_rotate_increments_version(self):
        vault = _vault()
        vault.store("s1", "old-secret")

        reference = vault.rotate("s1", "new-secret")

        assert reference.version == "2"

    def test_rotate_marks_metadata_rotated(self):
        vault = _vault()
        vault.store("s1", "old-secret")

        vault.rotate("s1", "new-secret")

        assert vault.metadata("s1").rotated is True

    def test_rotate_preserves_created_at(self):
        vault = _vault()
        vault.store("s1", "old-secret")
        created_at = vault.metadata("s1").created_at

        vault.rotate("s1", "new-secret")

        assert vault.metadata("s1").created_at == created_at

    def test_rotate_preserves_expiry_when_not_given(self):
        vault = _vault()
        expires_at = BASE_TIME + timedelta(days=30)
        vault.store("s1", "old-secret", expires_at=expires_at)

        vault.rotate("s1", "new-secret")

        assert vault.metadata("s1").expires_at == expires_at

    def test_rotate_can_replace_expiry(self):
        vault = _vault()
        vault.store(
            "s1", "old-secret",
            expires_at=BASE_TIME + timedelta(days=30),
        )
        new_expiry = BASE_TIME + timedelta(days=60)

        vault.rotate("s1", "new-secret", expires_at=new_expiry)

        assert vault.metadata("s1").expires_at == new_expiry

    def test_rotate_unknown_raises(self):
        vault = _vault()

        with pytest.raises(KeyError):
            vault.rotate("does-not-exist", "new-secret")

    def test_publishes_secret_rotated_without_value(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("secret_rotated", events.append)
        vault = _vault(event_bus=bus)
        vault.store("s1", "old-secret")

        vault.rotate("s1", "new-secret")

        assert len(events) == 1
        assert "new-secret" not in str(events[0].payload)


# --- Deletion --------------------------------------------------------------


class TestDeletion:

    def test_delete_removes_secret(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        vault.delete("s1")

        assert vault.exists("s1") is False

    def test_delete_removes_underlying_value(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        vault.delete("s1")
        vault.store("s1", "different-secret")

        assert vault.fetch("s1") == "different-secret"

    def test_delete_unknown_raises(self):
        vault = _vault()

        with pytest.raises(KeyError):
            vault.delete("does-not-exist")

    def test_publishes_secret_deleted(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("secret_deleted", events.append)
        vault = _vault(event_bus=bus)
        vault.store("s1", "top-secret")

        vault.delete("s1")

        assert len(events) == 1


# --- Metadata retrieval ------------------------------------------------


class TestMetadataRetrieval:

    def test_metadata_of_new_secret(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        metadata = vault.metadata("s1")

        assert metadata.created_at == BASE_TIME
        assert metadata.expires_at is None
        assert metadata.rotated is False

    def test_metadata_with_expiry(self):
        vault = _vault()
        expires_at = BASE_TIME + timedelta(days=30)

        vault.store("s1", "top-secret", expires_at=expires_at)

        assert vault.metadata("s1").expires_at == expires_at

    def test_metadata_unknown_raises(self):
        vault = _vault()

        with pytest.raises(KeyError):
            vault.metadata("does-not-exist")

    def test_reference_lookup(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        assert vault.reference("s1").name == "s1"


# --- Provider abstraction ------------------------------------------------


class TestProviderAbstraction:

    def test_built_in_providers_constant(self):
        assert set(BUILT_IN_SECRET_VAULT_PROVIDERS) == {
            "InMemoryVault", "EnvironmentVariables",
        }

    def test_in_memory_vault_provider_directly(self):
        provider = InMemoryVaultProvider()

        provider.put("k", "v")

        assert provider.get("k") == "v"
        assert provider.exists("k") is True

        provider.delete("k")

        assert provider.exists("k") is False

    def test_environment_variables_provider_uses_injected_mapping(self):
        env: dict = {}
        provider = EnvironmentVariablesProvider(env=env)

        provider.put("MY_SECRET", "v")

        assert env["MY_SECRET"] == "v"
        assert provider.get("MY_SECRET") == "v"

        provider.delete("MY_SECRET")

        assert "MY_SECRET" not in env

    def test_environment_variables_provider_via_vault(self):
        vault = _vault()

        reference = vault.store(
            "s-env", "top-secret", provider="EnvironmentVariables"
        )

        assert reference.provider == "EnvironmentVariables"
        assert vault.fetch("s-env") == "top-secret"

    def test_custom_provider_can_be_registered_and_used(self):
        class _CustomProvider:
            def __init__(self):
                self.values = {}

            def put(self, name, value):
                self.values[name] = value

            def get(self, name):
                return self.values.get(name)

            def delete(self, name):
                self.values.pop(name, None)

            def exists(self, name):
                return name in self.values

        vault = _vault()
        custom = _CustomProvider()
        vault.register_provider("Custom", custom)

        reference = vault.store(
            "s-custom", "top-secret", provider="Custom"
        )

        assert reference.provider == "Custom"
        assert vault.fetch("s-custom") == "top-secret"
        assert custom.values == {"s-custom": "top-secret"}

    def test_store_with_unknown_provider_raises(self):
        vault = _vault()

        with pytest.raises(ValueError, match="unknown secret vault"):
            vault.store("s1", "top-secret", provider="does-not-exist")


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_every_secret(self):
        vault = _vault()
        vault.store("s1", "top-secret")

        vault.clear()

        assert vault.exists("s1") is False

        with pytest.raises(KeyError):
            vault.metadata("s1")


# --- Authentication manager integration (isolated bridge) -------------------


class TestAuthenticationManagerIntegration:

    def test_register_local_credential_from_vault(self):
        from backend.observability.deployment_governance_authentication import (  # noqa: E501
            DeploymentAuthenticationManager,
        )

        vault = _vault()
        vault.store("p1-password", "hunter2")

        manager = DeploymentAuthenticationManager(clock=_clock)
        manager.register_local_credential_from_vault(
            "p1", vault, "p1-password"
        )

        result = manager.authenticate(
            "p1", "LOCAL", {"password": "hunter2"}
        )

        assert result.authenticated is True


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_secret_vault_returns_same_instance(self):
        assert get_secret_vault() is get_secret_vault()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecretVaultApi:

    def test_post_stores_secret(self, client):
        response = client.post(
            "/governance/security/secrets",
            params={"name": "api-s-1", "value": "top-secret"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "api-s-1"
        assert response.json()["version"] == "1"
        assert "top-secret" not in response.text

    def test_post_duplicate_returns_409(self, client):
        client.post(
            "/governance/security/secrets",
            params={"name": "api-s-2", "value": "v1"},
        )

        response = client.post(
            "/governance/security/secrets",
            params={"name": "api-s-2", "value": "v2"},
        )

        assert response.status_code == 409

    def test_get_metadata(self, client):
        client.post(
            "/governance/security/secrets",
            params={"name": "api-s-3", "value": "top-secret"},
        )

        response = client.get(
            "/governance/security/secrets/api-s-3/metadata"
        )

        assert response.status_code == 200
        assert response.json()["rotated"] is False
        assert "top-secret" not in response.text

    def test_get_metadata_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/secrets/does-not-exist/metadata"
        )

        assert response.status_code == 404

    def test_post_rotate(self, client):
        client.post(
            "/governance/security/secrets",
            params={"name": "api-s-4", "value": "v1"},
        )

        response = client.post(
            "/governance/security/secrets/api-s-4/rotate",
            params={"new_value": "v2"},
        )

        assert response.status_code == 200
        assert response.json()["version"] == "2"
        assert "v2" not in response.text

    def test_post_rotate_unknown_returns_404(self, client):
        response = client.post(
            "/governance/security/secrets/does-not-exist/rotate",
            params={"new_value": "v2"},
        )

        assert response.status_code == 404

    def test_delete_removes_secret(self, client):
        client.post(
            "/governance/security/secrets",
            params={"name": "api-s-5", "value": "top-secret"},
        )

        response = client.delete(
            "/governance/security/secrets/api-s-5"
        )

        assert response.status_code == 200
        assert response.json() == {"removed": "api-s-5"}

    def test_delete_unknown_returns_404(self, client):
        response = client.delete(
            "/governance/security/secrets/does-not-exist"
        )

        assert response.status_code == 404
