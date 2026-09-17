from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertSeverity,
)
from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelService,
    GovernanceIntegrityNotificationChannelType,
    InMemoryGovernanceIntegrityNotificationChannelRepository,
)
from backend.observability.deployment_governance_notification_preferences import (
    GovernanceIntegrityNotificationPreference,
    GovernanceIntegrityNotificationPreferenceService,
    InMemoryGovernanceIntegrityNotificationPreferenceRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_notification_preferences import (
    SQLiteGovernanceIntegrityNotificationPreferenceRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


class Harness:
    def __init__(self, *, clock=None) -> None:
        self.channel_repository = (
            InMemoryGovernanceIntegrityNotificationChannelRepository()
        )

        self.channel_service = GovernanceIntegrityNotificationChannelService(
            self.channel_repository
        )

        self.repository = (
            InMemoryGovernanceIntegrityNotificationPreferenceRepository()
        )

        self.service = GovernanceIntegrityNotificationPreferenceService(
            self.repository, self.channel_service, clock=clock
        )

    def add_channel(
        self,
        name: str,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        self.channel_service.create(name, channel_type, f"dest-{name}")


# --- Model -------------------------------------------------------------


def test_preference_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityNotificationPreference(
            name="  ",
            minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
            channels=("email",),
            enabled=True,
            created_at=BASE_TIME,
        )


def test_preference_rejects_empty_channels() -> None:
    with pytest.raises(ValueError, match="channels must not be empty"):
        GovernanceIntegrityNotificationPreference(
            name="warning",
            minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
            channels=(),
            enabled=True,
            created_at=BASE_TIME,
        )


def test_preference_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityNotificationPreference(
            name="warning",
            minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
            channels=("email",),
            enabled=True,
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_rejects_duplicate_name() -> None:
    repository = InMemoryGovernanceIntegrityNotificationPreferenceRepository()

    preference = GovernanceIntegrityNotificationPreference(
        name="warning",
        minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
        channels=("email",),
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(preference)

    with pytest.raises(Exception):
        repository.save(preference)


def test_repository_update_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityNotificationPreferenceRepository()

    with pytest.raises(KeyError):
        repository.update(
            GovernanceIntegrityNotificationPreference(
                name="missing",
                minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
                channels=("email",),
                enabled=True,
                created_at=BASE_TIME,
            )
        )


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityNotificationPreferenceRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_list_orders_by_name() -> None:
    repository = InMemoryGovernanceIntegrityNotificationPreferenceRepository()

    repository.save(
        GovernanceIntegrityNotificationPreference(
            name="zeta",
            minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
            channels=("email",),
            enabled=True,
            created_at=BASE_TIME,
        )
    )
    repository.save(
        GovernanceIntegrityNotificationPreference(
            name="alpha",
            minimum_severity=GovernanceIntegrityAlertSeverity.WARNING,
            channels=("email",),
            enabled=True,
            created_at=BASE_TIME,
        )
    )

    names = [preference.name for preference in repository.list()]

    assert names == ["alpha", "zeta"]


# --- Service: create -----------------------------------------------------


def test_create_returns_enabled_preference() -> None:
    harness = Harness()

    preference = harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    assert preference.enabled


def test_create_rejects_duplicate_name() -> None:
    harness = Harness()

    harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    with pytest.raises(ValueError):
        harness.service.create(
            "warning-and-up",
            GovernanceIntegrityAlertSeverity.CRITICAL,
            ("slack",),
        )


# --- Service: update -----------------------------------------------------


def test_update_changes_channels() -> None:
    harness = Harness()

    harness.service.create(
        "critical",
        GovernanceIntegrityAlertSeverity.CRITICAL,
        ("slack",),
    )

    updated = harness.service.update(
        "critical", channels=("email", "slack")
    )

    assert updated.channels == ("email", "slack")

    persisted = harness.service.get("critical")

    assert persisted is not None
    assert persisted.channels == ("email", "slack")


def test_update_changes_minimum_severity() -> None:
    harness = Harness()

    harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    updated = harness.service.update(
        "warning-and-up",
        minimum_severity=GovernanceIntegrityAlertSeverity.CRITICAL,
    )

    assert (
        updated.minimum_severity
        is GovernanceIntegrityAlertSeverity.CRITICAL
    )


def test_update_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.update("missing", channels=("email",))


# --- Service: delete/get/list ---------------------------------------------


def test_delete_removes_preference() -> None:
    harness = Harness()

    harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    harness.service.delete("warning-and-up")

    assert harness.service.get("warning-and-up") is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


# --- Service: resolve --------------------------------------------------


def test_resolve_matches_severity_tiers() -> None:
    harness = Harness()

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_channel(
        "slack", GovernanceIntegrityNotificationChannelType.SLACK
    )

    harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )
    harness.service.create(
        "critical-only",
        GovernanceIntegrityAlertSeverity.CRITICAL,
        ("email", "slack"),
    )

    info_channels = harness.service.resolve(
        GovernanceIntegrityAlertSeverity.INFO
    )
    warning_channels = harness.service.resolve(
        GovernanceIntegrityAlertSeverity.WARNING
    )
    critical_channels = harness.service.resolve(
        GovernanceIntegrityAlertSeverity.CRITICAL
    )

    assert info_channels == ()
    assert {channel.name for channel in warning_channels} == {"email"}
    assert {channel.name for channel in critical_channels} == {
        "email",
        "slack",
    }


def test_resolve_ignores_disabled_preference() -> None:
    harness = Harness()

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    harness.service.update("warning-and-up", enabled=False)

    assert (
        harness.service.resolve(GovernanceIntegrityAlertSeverity.CRITICAL)
        == ()
    )


def test_resolve_excludes_disabled_channel() -> None:
    harness = Harness()

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    harness.service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    harness.channel_service.disable("email")

    assert (
        harness.service.resolve(GovernanceIntegrityAlertSeverity.WARNING)
        == ()
    )


def test_resolve_deduplicates_channels_across_preferences() -> None:
    harness = Harness()

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    harness.service.create(
        "a",
        GovernanceIntegrityAlertSeverity.INFO,
        ("email",),
    )
    harness.service.create(
        "b",
        GovernanceIntegrityAlertSeverity.INFO,
        ("email",),
    )

    channels = harness.service.resolve(
        GovernanceIntegrityAlertSeverity.CRITICAL
    )

    assert len(channels) == 1


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "notification-preferences.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityNotificationPreferenceRepository(
        database
    )

    repository.save(
        GovernanceIntegrityNotificationPreference(
            name="critical",
            minimum_severity=GovernanceIntegrityAlertSeverity.CRITICAL,
            channels=("email", "slack"),
            enabled=True,
            created_at=BASE_TIME,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityNotificationPreferenceRepository(
            reloaded_database
        )
    )

    preference = reloaded_repository.get("critical")

    assert preference is not None
    assert preference.channels == ("email", "slack")


def test_sqlite_repository_save_rejects_duplicate(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "notification-preferences-dup.db"
        )
    )

    repository = SQLiteGovernanceIntegrityNotificationPreferenceRepository(
        database
    )

    preference = GovernanceIntegrityNotificationPreference(
        name="critical",
        minimum_severity=GovernanceIntegrityAlertSeverity.CRITICAL,
        channels=("email",),
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(preference)

    with pytest.raises(Exception):
        repository.save(preference)


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_preference_service_over_sqlite(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "notification-preferences-runtime.db"
        )
    )

    channel_service = runtime.build_integrity_notification_channel_service()
    channel_service.create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    preference_service = (
        runtime.build_integrity_notification_preference_service()
    )
    preference_service.create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "notification-preferences-runtime.db"
        )
    )

    reloaded_preference_service = (
        reloaded_runtime.build_integrity_notification_preference_service()
    )

    preference = reloaded_preference_service.get("warning-and-up")

    assert preference is not None
    assert preference.channels == ("email",)
