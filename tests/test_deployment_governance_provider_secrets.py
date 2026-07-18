from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    EmailProvider,
)
from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecrets,
    GovernanceIntegrityProviderSecretsAlreadyExistsError,
    GovernanceIntegrityProviderSecretsService,
    InMemoryGovernanceIntegrityProviderSecretsRepository,
)
from backend.observability.sqlite_deployment_governance_provider_secrets import (
    SQLiteGovernanceIntegrityProviderSecretsRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
SLACK = GovernanceIntegrityNotificationChannelType.SLACK
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


def _registry_with_webhook() -> GovernanceIntegrityProviderRegistry:
    registry = GovernanceIntegrityProviderRegistry()
    registry.register(WEBHOOK, EmailProvider())
    return registry


# --- Model ---------------------------------------------------------------


def test_secrets_values_are_immutable() -> None:
    secrets = GovernanceIntegrityProviderSecrets(
        channel_type=WEBHOOK,
        values={"api_key": "abc123"},
        updated_at=BASE_TIME,
    )

    assert isinstance(secrets.values, MappingProxyType)

    with pytest.raises(TypeError):
        secrets.values["api_key"] = "xyz789"


def test_secrets_rejects_naive_updated_at() -> None:
    with pytest.raises(
        ValueError, match="updated_at must be timezone-aware"
    ):
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK,
            values={},
            updated_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_secrets_to_dict() -> None:
    secrets = GovernanceIntegrityProviderSecrets(
        channel_type=WEBHOOK,
        values={"api_key": "abc123"},
        updated_at=BASE_TIME,
    )

    assert secrets.to_dict() == {
        "channel_type": "webhook",
        "values": {"api_key": "abc123"},
        "updated_at": BASE_TIME.isoformat(),
    }


def test_secrets_empty() -> None:
    secrets = GovernanceIntegrityProviderSecrets.empty(
        WEBHOOK, checked_at=BASE_TIME
    )

    assert dict(secrets.values) == {}
    assert secrets.channel_type is WEBHOOK


# --- Repository: InMemory ------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    secrets = GovernanceIntegrityProviderSecrets(
        channel_type=WEBHOOK,
        values={"api_key": "abc123"},
        updated_at=BASE_TIME,
    )

    repository.save(secrets)

    assert repository.get(WEBHOOK) == secrets


def test_repository_save_rejects_duplicate() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    secrets = GovernanceIntegrityProviderSecrets(
        channel_type=WEBHOOK, values={}, updated_at=BASE_TIME
    )

    repository.save(secrets)

    with pytest.raises(GovernanceIntegrityProviderSecretsAlreadyExistsError):
        repository.save(secrets)


def test_repository_update_replaces_values() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    repository.save(
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK,
            values={"api_key": "abc123"},
            updated_at=BASE_TIME,
        )
    )

    updated = GovernanceIntegrityProviderSecrets(
        channel_type=WEBHOOK,
        values={"api_key": "xyz789"},
        updated_at=BASE_TIME,
    )

    repository.update(updated)

    assert dict(repository.get(WEBHOOK).values) == {"api_key": "xyz789"}


def test_repository_update_raises_for_missing() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    with pytest.raises(KeyError):
        repository.update(
            GovernanceIntegrityProviderSecrets(
                channel_type=WEBHOOK, values={}, updated_at=BASE_TIME
            )
        )


def test_repository_delete_removes_secrets() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    repository.save(
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK, values={}, updated_at=BASE_TIME
        )
    )

    repository.delete(WEBHOOK)

    assert not repository.exists(WEBHOOK)


def test_repository_delete_raises_for_missing() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    with pytest.raises(KeyError):
        repository.delete(WEBHOOK)


def test_repository_list_ordered_by_channel_type() -> None:
    repository = InMemoryGovernanceIntegrityProviderSecretsRepository()

    repository.save(
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK, values={}, updated_at=BASE_TIME
        )
    )
    repository.save(
        GovernanceIntegrityProviderSecrets(
            channel_type=EMAIL, values={}, updated_at=BASE_TIME
        )
    )

    secrets_list = repository.list()

    assert [s.channel_type for s in secrets_list] == [EMAIL, WEBHOOK]


# --- Service ----------------------------------------------------------


def test_service_create_stores_values() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
        clock=lambda: BASE_TIME,
    )

    secrets = service.create(WEBHOOK, {"api_key": "abc123"})

    assert secrets.values["api_key"] == "abc123"


def test_service_create_raises_when_provider_missing() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        GovernanceIntegrityProviderRegistry(),
    )

    with pytest.raises(LookupError):
        service.create(WEBHOOK, {})


def test_service_create_rejects_duplicate() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
    )

    service.create(WEBHOOK, {"api_key": "abc123"})

    with pytest.raises(ValueError):
        service.create(WEBHOOK, {"api_key": "xyz789"})


def test_service_update_replaces_complete_secret_set() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
    )

    service.create(
        WEBHOOK, {"api_key": "abc123", "webhook_signing_secret": "sig"}
    )

    updated = service.update(WEBHOOK, {"api_key": "xyz789"})

    assert dict(updated.values) == {"api_key": "xyz789"}
    assert dict(service.get(WEBHOOK).values) == {"api_key": "xyz789"}


def test_service_update_raises_for_missing() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
    )

    with pytest.raises(KeyError):
        service.update(WEBHOOK, {"api_key": "xyz789"})


def test_service_delete() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
    )

    service.create(WEBHOOK, {})
    service.delete(WEBHOOK)

    assert service.get(WEBHOOK) is None


def test_service_list() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
    )

    service.create(WEBHOOK, {"api_key": "abc123"})

    assert len(service.list()) == 1


def test_service_resolve_returns_empty_when_missing() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
        clock=lambda: BASE_TIME,
    )

    resolved = service.resolve(WEBHOOK)

    assert resolved == GovernanceIntegrityProviderSecrets(
        channel_type=WEBHOOK,
        values={},
        updated_at=BASE_TIME,
    )


def test_service_resolve_returns_stored_secrets() -> None:
    service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        _registry_with_webhook(),
    )

    service.create(WEBHOOK, {"api_key": "abc123"})

    resolved = service.resolve(WEBHOOK)

    assert dict(resolved.values) == {"api_key": "abc123"}


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "provider-secrets.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityProviderSecretsRepository(
        database
    )

    repository.save(
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK,
            values={"api_key": "abc123"},
            updated_at=BASE_TIME,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityProviderSecretsRepository(
            reloaded_database
        )
    )

    secrets = reloaded_repository.get(WEBHOOK)

    assert secrets is not None
    assert dict(secrets.values) == {"api_key": "abc123"}


def test_sqlite_repository_update_and_delete(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "provider-secrets-crud.db"
        )
    )

    repository = SQLiteGovernanceIntegrityProviderSecretsRepository(
        database
    )

    repository.save(
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK,
            values={"api_key": "abc123"},
            updated_at=BASE_TIME,
        )
    )

    repository.update(
        GovernanceIntegrityProviderSecrets(
            channel_type=WEBHOOK,
            values={"api_key": "xyz789"},
            updated_at=BASE_TIME,
        )
    )

    assert dict(repository.get(WEBHOOK).values) == {"api_key": "xyz789"}

    repository.delete(WEBHOOK)

    assert repository.get(WEBHOOK) is None


# --- Runtime ---------------------------------------------------------------


def test_runtime_secrets_persist_across_reload(tmp_path) -> None:
    database_path = tmp_path / "provider-secrets-runtime.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.build_integrity_provider_secrets_service().create(
        WEBHOOK, {"api_key": "abc123"}
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    secrets = (
        reloaded_runtime
        .build_integrity_provider_secrets_service()
        .get(WEBHOOK)
    )

    assert secrets is not None
    assert dict(secrets.values) == {"api_key": "abc123"}
