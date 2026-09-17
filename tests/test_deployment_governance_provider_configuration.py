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
from backend.observability.deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfiguration,
    GovernanceIntegrityProviderConfigurationAlreadyExistsError,
    GovernanceIntegrityProviderConfigurationService,
    InMemoryGovernanceIntegrityProviderConfigurationRepository,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.sqlite_deployment_governance_provider_configuration import (
    SQLiteGovernanceIntegrityProviderConfigurationRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
SLACK = GovernanceIntegrityNotificationChannelType.SLACK
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


def _registry_with_email() -> GovernanceIntegrityProviderRegistry:
    registry = GovernanceIntegrityProviderRegistry()
    registry.register(EMAIL, EmailProvider())
    return registry


# --- Model ---------------------------------------------------------------


def test_configuration_values_are_immutable() -> None:
    configuration = GovernanceIntegrityProviderConfiguration(
        channel_type=EMAIL,
        values={"timeout": "30"},
        updated_at=BASE_TIME,
    )

    assert isinstance(configuration.values, MappingProxyType)

    with pytest.raises(TypeError):
        configuration.values["timeout"] = "60"


def test_configuration_rejects_naive_updated_at() -> None:
    with pytest.raises(
        ValueError, match="updated_at must be timezone-aware"
    ):
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL,
            values={},
            updated_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_configuration_to_dict() -> None:
    configuration = GovernanceIntegrityProviderConfiguration(
        channel_type=EMAIL,
        values={"timeout": "30"},
        updated_at=BASE_TIME,
    )

    assert configuration.to_dict() == {
        "channel_type": "email",
        "values": {"timeout": "30"},
        "updated_at": BASE_TIME.isoformat(),
    }


def test_configuration_empty() -> None:
    configuration = GovernanceIntegrityProviderConfiguration.empty(
        EMAIL, checked_at=BASE_TIME
    )

    assert dict(configuration.values) == {}
    assert configuration.channel_type is EMAIL


# --- Repository: InMemory ------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    configuration = GovernanceIntegrityProviderConfiguration(
        channel_type=EMAIL,
        values={"timeout": "30"},
        updated_at=BASE_TIME,
    )

    repository.save(configuration)

    assert repository.get(EMAIL) == configuration


def test_repository_save_rejects_duplicate() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    configuration = GovernanceIntegrityProviderConfiguration(
        channel_type=EMAIL, values={}, updated_at=BASE_TIME
    )

    repository.save(configuration)

    with pytest.raises(
        GovernanceIntegrityProviderConfigurationAlreadyExistsError
    ):
        repository.save(configuration)


def test_repository_update_replaces_values() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    repository.save(
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL,
            values={"timeout": "30"},
            updated_at=BASE_TIME,
        )
    )

    updated = GovernanceIntegrityProviderConfiguration(
        channel_type=EMAIL,
        values={"timeout": "60"},
        updated_at=BASE_TIME,
    )

    repository.update(updated)

    assert dict(repository.get(EMAIL).values) == {"timeout": "60"}


def test_repository_update_raises_for_missing() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    with pytest.raises(KeyError):
        repository.update(
            GovernanceIntegrityProviderConfiguration(
                channel_type=EMAIL, values={}, updated_at=BASE_TIME
            )
        )


def test_repository_delete_removes_configuration() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    repository.save(
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL, values={}, updated_at=BASE_TIME
        )
    )

    repository.delete(EMAIL)

    assert not repository.exists(EMAIL)


def test_repository_delete_raises_for_missing() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    with pytest.raises(KeyError):
        repository.delete(EMAIL)


def test_repository_list_ordered_by_channel_type() -> None:
    repository = InMemoryGovernanceIntegrityProviderConfigurationRepository()

    repository.save(
        GovernanceIntegrityProviderConfiguration(
            channel_type=WEBHOOK, values={}, updated_at=BASE_TIME
        )
    )
    repository.save(
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL, values={}, updated_at=BASE_TIME
        )
    )

    configurations = repository.list()

    assert [c.channel_type for c in configurations] == [EMAIL, WEBHOOK]


# --- Service ----------------------------------------------------------


def test_service_create_stores_values() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
        clock=lambda: BASE_TIME,
    )

    config = service.create(EMAIL, {"timeout": "30"})

    assert config.values["timeout"] == "30"


def test_service_create_raises_when_provider_missing() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        GovernanceIntegrityProviderRegistry(),
    )

    with pytest.raises(LookupError):
        service.create(EMAIL, {})


def test_service_create_rejects_duplicate() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
    )

    service.create(EMAIL, {"timeout": "30"})

    with pytest.raises(ValueError):
        service.create(EMAIL, {"timeout": "60"})


def test_service_update_replaces_complete_configuration() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
    )

    service.create(EMAIL, {"timeout": "30", "sender": "a@example.com"})

    updated = service.update(EMAIL, {"timeout": "60"})

    assert dict(updated.values) == {"timeout": "60"}
    assert dict(service.get(EMAIL).values) == {"timeout": "60"}


def test_service_update_raises_for_missing() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
    )

    with pytest.raises(KeyError):
        service.update(EMAIL, {"timeout": "60"})


def test_service_delete() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
    )

    service.create(EMAIL, {})
    service.delete(EMAIL)

    assert service.get(EMAIL) is None


def test_service_list() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
    )

    service.create(EMAIL, {"timeout": "30"})

    assert len(service.list()) == 1


def test_service_resolve_returns_empty_when_missing() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
        clock=lambda: BASE_TIME,
    )

    resolved = service.resolve(EMAIL)

    assert resolved == GovernanceIntegrityProviderConfiguration(
        channel_type=EMAIL,
        values={},
        updated_at=BASE_TIME,
    )


def test_service_resolve_returns_stored_configuration() -> None:
    service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        _registry_with_email(),
    )

    service.create(EMAIL, {"timeout": "30"})

    resolved = service.resolve(EMAIL)

    assert dict(resolved.values) == {"timeout": "30"}


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "provider-configuration.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityProviderConfigurationRepository(
        database
    )

    repository.save(
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL,
            values={"timeout": "30", "sender": "ops@example.com"},
            updated_at=BASE_TIME,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityProviderConfigurationRepository(
            reloaded_database
        )
    )

    configuration = reloaded_repository.get(EMAIL)

    assert configuration is not None
    assert dict(configuration.values) == {
        "timeout": "30",
        "sender": "ops@example.com",
    }


def test_sqlite_repository_update_and_delete(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "provider-configuration-crud.db"
        )
    )

    repository = SQLiteGovernanceIntegrityProviderConfigurationRepository(
        database
    )

    repository.save(
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL,
            values={"timeout": "30"},
            updated_at=BASE_TIME,
        )
    )

    repository.update(
        GovernanceIntegrityProviderConfiguration(
            channel_type=EMAIL,
            values={"timeout": "60"},
            updated_at=BASE_TIME,
        )
    )

    assert dict(repository.get(EMAIL).values) == {"timeout": "60"}

    repository.delete(EMAIL)

    assert repository.get(EMAIL) is None


# --- Runtime ---------------------------------------------------------------


def test_runtime_configuration_persists_across_reload(tmp_path) -> None:
    database_path = tmp_path / "provider-configuration-runtime.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.build_integrity_provider_configuration_service().create(
        EMAIL, {"timeout": "30"}
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    configuration = (
        reloaded_runtime
        .build_integrity_provider_configuration_service()
        .get(EMAIL)
    )

    assert configuration is not None
    assert dict(configuration.values) == {"timeout": "30"}
