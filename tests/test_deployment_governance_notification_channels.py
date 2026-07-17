from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
    GovernanceIntegrityNotificationChannelService,
    GovernanceIntegrityNotificationChannelType,
    InMemoryGovernanceIntegrityNotificationChannelRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_notification_channels import (
    SQLiteGovernanceIntegrityNotificationChannelRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


class Harness:
    def __init__(self, *, clock=None) -> None:
        self.repository = (
            InMemoryGovernanceIntegrityNotificationChannelRepository()
        )

        self.service = GovernanceIntegrityNotificationChannelService(
            self.repository, clock=clock
        )


# --- Model -------------------------------------------------------------


def test_channel_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityNotificationChannel(
            name="  ",
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            destination="ops@example.com",
            enabled=True,
            created_at=BASE_TIME,
        )


def test_channel_rejects_empty_destination() -> None:
    with pytest.raises(
        ValueError, match="destination must not be empty"
    ):
        GovernanceIntegrityNotificationChannel(
            name="ops-email",
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            destination="  ",
            enabled=True,
            created_at=BASE_TIME,
        )


def test_channel_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityNotificationChannel(
            name="ops-email",
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            destination="ops@example.com",
            enabled=True,
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_rejects_duplicate_name() -> None:
    repository = InMemoryGovernanceIntegrityNotificationChannelRepository()

    channel = GovernanceIntegrityNotificationChannel(
        name="ops-email",
        channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
        destination="ops@example.com",
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(channel)

    with pytest.raises(Exception):
        repository.save(channel)


def test_repository_update_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityNotificationChannelRepository()

    with pytest.raises(KeyError):
        repository.update(
            GovernanceIntegrityNotificationChannel(
                name="missing",
                channel_type=(
                    GovernanceIntegrityNotificationChannelType.EMAIL
                ),
                destination="ops@example.com",
                enabled=True,
                created_at=BASE_TIME,
            )
        )


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityNotificationChannelRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_list_orders_by_name() -> None:
    repository = InMemoryGovernanceIntegrityNotificationChannelRepository()

    repository.save(
        GovernanceIntegrityNotificationChannel(
            name="zeta",
            channel_type=GovernanceIntegrityNotificationChannelType.SLACK,
            destination="#zeta",
            enabled=True,
            created_at=BASE_TIME,
        )
    )
    repository.save(
        GovernanceIntegrityNotificationChannel(
            name="alpha",
            channel_type=GovernanceIntegrityNotificationChannelType.SLACK,
            destination="#alpha",
            enabled=True,
            created_at=BASE_TIME,
        )
    )

    names = [channel.name for channel in repository.list()]

    assert names == ["alpha", "zeta"]


# --- Service: create -----------------------------------------------------


def test_create_returns_enabled_channel() -> None:
    harness = Harness()

    channel = harness.service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    assert channel.enabled


def test_create_rejects_duplicate_name() -> None:
    harness = Harness()

    harness.service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    with pytest.raises(ValueError):
        harness.service.create(
            "ops-email",
            GovernanceIntegrityNotificationChannelType.EMAIL,
            "other@example.com",
        )


# --- Service: enable/disable ------------------------------------------


def test_disable_then_enable_changes_state() -> None:
    harness = Harness()

    harness.service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    disabled = harness.service.disable("ops-email")

    assert disabled.enabled is False

    enabled = harness.service.enable("ops-email")

    assert enabled.enabled is True


def test_disable_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.disable("missing")


# --- Service: update_destination --------------------------------------


def test_update_destination_persists_new_value() -> None:
    harness = Harness()

    harness.service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    harness.service.update_destination(
        "ops-email", "admin@example.com"
    )

    persisted = harness.repository.get("ops-email")

    assert persisted is not None
    assert persisted.destination == "admin@example.com"


def test_update_destination_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.update_destination(
            "missing", "admin@example.com"
        )


# --- Service: delete/get/list -------------------------------------------


def test_delete_removes_channel() -> None:
    harness = Harness()

    harness.service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    harness.service.delete("ops-email")

    assert harness.service.get("ops-email") is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


# --- Service: enabled_channels -------------------------------------------


def test_enabled_channels_excludes_disabled() -> None:
    harness = Harness()

    harness.service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )
    harness.service.create(
        "ops-slack",
        GovernanceIntegrityNotificationChannelType.SLACK,
        "#ops",
    )
    harness.service.create(
        "ops-webhook",
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        "https://example.com/hook",
    )

    harness.service.disable("ops-slack")

    assert len(harness.service.enabled_channels()) == 2


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "notification-channels.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityNotificationChannelRepository(
        database
    )

    repository.save(
        GovernanceIntegrityNotificationChannel(
            name="ops-email",
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            destination="ops@example.com",
            enabled=True,
            created_at=BASE_TIME,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityNotificationChannelRepository(
            reloaded_database
        )
    )

    channel = reloaded_repository.get("ops-email")

    assert channel is not None
    assert channel.destination == "ops@example.com"


def test_sqlite_repository_save_rejects_duplicate(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "notification-channels-dup.db"
        )
    )

    repository = SQLiteGovernanceIntegrityNotificationChannelRepository(
        database
    )

    channel = GovernanceIntegrityNotificationChannel(
        name="ops-email",
        channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
        destination="ops@example.com",
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(channel)

    with pytest.raises(Exception):
        repository.save(channel)


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_channel_service_over_sqlite(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "notification-channels-runtime.db"
        )
    )

    service = runtime.build_integrity_notification_channel_service()

    service.create(
        "ops-email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "notification-channels-runtime.db"
        )
    )

    reloaded_service = (
        reloaded_runtime.build_integrity_notification_channel_service()
    )

    channel = reloaded_service.get("ops-email")

    assert channel is not None
    assert channel.destination == "ops@example.com"
