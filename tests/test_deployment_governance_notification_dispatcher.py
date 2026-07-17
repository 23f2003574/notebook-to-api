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
from backend.observability.deployment_governance_notification_dispatcher import (
    GovernanceIntegrityDispatchStatus,
    GovernanceIntegrityNotificationDispatch,
    GovernanceIntegrityNotificationDispatcher,
    InMemoryGovernanceIntegrityNotificationDispatchRepository,
)
from backend.observability.deployment_governance_notifications import (
    GovernanceIntegrityNotification,
    GovernanceIntegrityNotificationStatus,
    InMemoryGovernanceIntegrityNotificationRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_notification_dispatcher import (
    SQLiteGovernanceIntegrityNotificationDispatchRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_notification(notification_id: str) -> GovernanceIntegrityNotification:
    return GovernanceIntegrityNotification(
        notification_id=notification_id,
        alert_id=f"alert-{notification_id}",
        severity=GovernanceIntegrityAlertSeverity.WARNING,
        message="boom",
        status=GovernanceIntegrityNotificationStatus.PENDING,
        created_at=BASE_TIME,
    )


class Harness:
    def __init__(self, *, clock=None, uuid_factory=None) -> None:
        self.notification_repository = (
            InMemoryGovernanceIntegrityNotificationRepository()
        )

        self.channel_repository = (
            InMemoryGovernanceIntegrityNotificationChannelRepository()
        )

        self.channel_service = GovernanceIntegrityNotificationChannelService(
            self.channel_repository
        )

        self.dispatch_repository = (
            InMemoryGovernanceIntegrityNotificationDispatchRepository()
        )

        self.dispatcher = GovernanceIntegrityNotificationDispatcher(
            self.notification_repository,
            self.channel_service,
            self.dispatch_repository,
            clock=clock,
            uuid_factory=uuid_factory,
        )

    def add_notifications(self, *ids: str) -> None:
        for notification_id in ids:
            self.notification_repository.save(
                make_notification(notification_id)
            )

    def add_channel(
        self,
        name: str,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        self.channel_service.create(name, channel_type, f"dest-{name}")


# --- Model -------------------------------------------------------------


def test_dispatch_rejects_empty_dispatch_id() -> None:
    with pytest.raises(ValueError, match="dispatch_id must not be empty"):
        GovernanceIntegrityNotificationDispatch(
            dispatch_id="  ",
            notification_id="n1",
            channel_name="email",
            status=GovernanceIntegrityDispatchStatus.QUEUED,
            created_at=BASE_TIME,
        )


def test_dispatch_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityNotificationDispatch(
            dispatch_id="d1",
            notification_id="n1",
            channel_name="email",
            status=GovernanceIntegrityDispatchStatus.QUEUED,
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityNotificationDispatchRepository()

    dispatch = GovernanceIntegrityNotificationDispatch(
        dispatch_id="d1",
        notification_id="n1",
        channel_name="email",
        status=GovernanceIntegrityDispatchStatus.QUEUED,
        created_at=BASE_TIME,
    )

    repository.save(dispatch)

    assert repository.get("d1") == dispatch


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityNotificationDispatchRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_clear_empties_store() -> None:
    repository = InMemoryGovernanceIntegrityNotificationDispatchRepository()

    repository.save(
        GovernanceIntegrityNotificationDispatch(
            dispatch_id="d1",
            notification_id="n1",
            channel_name="email",
            status=GovernanceIntegrityDispatchStatus.QUEUED,
            created_at=BASE_TIME,
        )
    )

    repository.clear()

    assert repository.list() == ()


# --- Service: dispatch_pending ---------------------------------------------


def test_dispatch_matches_every_notification_to_every_channel() -> None:
    harness = Harness()

    harness.add_notifications("n1", "n2")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_channel(
        "slack", GovernanceIntegrityNotificationChannelType.SLACK
    )

    dispatches = harness.dispatcher.dispatch_pending()

    assert len(dispatches) == 4


def test_disabled_channel_is_ignored() -> None:
    harness = Harness()

    harness.add_notifications("n1", "n2")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_channel(
        "slack", GovernanceIntegrityNotificationChannelType.SLACK
    )

    harness.channel_service.disable("slack")

    dispatches = harness.dispatcher.dispatch_pending()

    assert len(dispatches) == 2
    assert all(
        dispatch.channel_name == "email" for dispatch in dispatches
    )


def test_running_twice_does_not_duplicate() -> None:
    harness = Harness()

    harness.add_notifications("n1", "n2")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_channel(
        "slack", GovernanceIntegrityNotificationChannelType.SLACK
    )

    first = harness.dispatcher.dispatch_pending()

    second = harness.dispatcher.dispatch_pending()

    assert len(first) == 4
    assert second == ()
    assert len(harness.dispatch_repository.list()) == 4


def test_dispatch_with_no_notifications_returns_empty_tuple() -> None:
    harness = Harness()

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    assert harness.dispatcher.dispatch_pending() == ()


def test_dispatch_with_no_enabled_channels_returns_empty_tuple() -> None:
    harness = Harness()

    harness.add_notifications("n1")

    assert harness.dispatcher.dispatch_pending() == ()


def test_dispatch_uses_injected_uuid_factory() -> None:
    ids = iter(["fixed-dispatch-id"])

    harness = Harness(uuid_factory=lambda: next(ids))

    harness.add_notifications("n1")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    dispatches = harness.dispatcher.dispatch_pending()

    assert len(dispatches) == 1
    assert dispatches[0].dispatch_id == "fixed-dispatch-id"


# --- Service: get/list/delete/clear -----------------------------------


def test_delete_removes_dispatch() -> None:
    harness = Harness()

    harness.add_notifications("n1")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    dispatches = harness.dispatcher.dispatch_pending()

    target = dispatches[0]

    harness.dispatcher.delete(target.dispatch_id)

    assert harness.dispatcher.get(target.dispatch_id) is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.dispatcher.delete("missing")


def test_clear_empties_repository() -> None:
    harness = Harness()

    harness.add_notifications("n1")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    harness.dispatcher.dispatch_pending()

    harness.dispatcher.clear()

    assert harness.dispatcher.list() == ()


def test_get_returns_none_for_missing_dispatch() -> None:
    harness = Harness()

    assert harness.dispatcher.get("missing") is None


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "notification-dispatches.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityNotificationDispatchRepository(
        database
    )

    repository.save(
        GovernanceIntegrityNotificationDispatch(
            dispatch_id="d1",
            notification_id="n1",
            channel_name="email",
            status=GovernanceIntegrityDispatchStatus.QUEUED,
            created_at=BASE_TIME,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityNotificationDispatchRepository(
            reloaded_database
        )
    )

    dispatch = reloaded_repository.get("d1")

    assert dispatch is not None
    assert dispatch.notification_id == "n1"
    assert dispatch.channel_name == "email"


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_dispatcher_over_sqlite(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "dispatcher-runtime.db"
        )
    )

    runtime.notification_repository.save(make_notification("n1"))

    channel_service = runtime.build_integrity_notification_channel_service()

    channel_service.create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    dispatcher = runtime.build_integrity_notification_dispatcher()

    dispatches = dispatcher.dispatch_pending()

    assert len(dispatches) == 1

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "dispatcher-runtime.db"
        )
    )

    reloaded_dispatcher = (
        reloaded_runtime.build_integrity_notification_dispatcher()
    )

    assert len(reloaded_dispatcher.list()) == 1
