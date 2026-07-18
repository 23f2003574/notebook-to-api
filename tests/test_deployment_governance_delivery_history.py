from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    EmailProvider,
    GovernanceIntegrityDeliveryEngine,
    GovernanceIntegrityDeliveryResult,
    GovernanceIntegrityDeliveryStatus,
)
from backend.observability.deployment_governance_delivery_history import (
    GovernanceIntegrityDeliveryHistoryRecord,
    GovernanceIntegrityDeliveryHistoryService,
    InMemoryGovernanceIntegrityDeliveryHistoryRepository,
)
from backend.observability.deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicyService,
    InMemoryGovernanceIntegrityDeliveryPolicyRepository,
)
from backend.observability.deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertSeverity,
)
from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
    GovernanceIntegrityNotificationChannelService,
    GovernanceIntegrityNotificationChannelType,
    InMemoryGovernanceIntegrityNotificationChannelRepository,
)
from backend.observability.deployment_governance_notification_dispatcher import (
    GovernanceIntegrityDispatchStatus,
    GovernanceIntegrityNotificationDispatch,
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
from backend.observability.deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfigurationService,
    InMemoryGovernanceIntegrityProviderConfigurationRepository,
)
from backend.observability.deployment_governance_provider_authentication import (
    GovernanceIntegrityProviderAuthenticationService,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.deployment_governance_provider_requests import (
    GovernanceIntegrityProviderRequestService,
)
from backend.observability.deployment_governance_provider_responses import (
    GovernanceIntegrityProviderResponseService,
)
from backend.observability.deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecretsService,
    InMemoryGovernanceIntegrityProviderSecretsRepository,
)
from backend.observability.sqlite_deployment_governance_delivery_history import (
    SQLiteGovernanceIntegrityDeliveryHistoryRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


class Harness:
    def __init__(self) -> None:
        self.dispatch_repository = (
            InMemoryGovernanceIntegrityNotificationDispatchRepository()
        )

        self.notification_repository = (
            InMemoryGovernanceIntegrityNotificationRepository()
        )

        self.channel_repository = (
            InMemoryGovernanceIntegrityNotificationChannelRepository()
        )

        self.channel_service = GovernanceIntegrityNotificationChannelService(
            self.channel_repository
        )

        self.policy_repository = (
            InMemoryGovernanceIntegrityDeliveryPolicyRepository()
        )

        self.policy_service = GovernanceIntegrityDeliveryPolicyService(
            self.policy_repository, self.channel_service
        )

        self.provider_registry = GovernanceIntegrityProviderRegistry()

        self.provider_registry.register(
            GovernanceIntegrityNotificationChannelType.EMAIL,
            EmailProvider(),
        )

        self.configuration_service = (
            GovernanceIntegrityProviderConfigurationService(
                InMemoryGovernanceIntegrityProviderConfigurationRepository(),
                self.provider_registry,
            )
        )

        self.secrets_service = GovernanceIntegrityProviderSecretsService(
            InMemoryGovernanceIntegrityProviderSecretsRepository(),
            self.provider_registry,
        )

        self.authentication_service = (
            GovernanceIntegrityProviderAuthenticationService(
                self.configuration_service,
                self.secrets_service,
                self.provider_registry,
            )
        )

        self.request_service = GovernanceIntegrityProviderRequestService(
            self.authentication_service,
            self.configuration_service,
            self.policy_service,
            self.provider_registry,
        )

        self.response_service = GovernanceIntegrityProviderResponseService(
            self.provider_registry
        )

        self.engine = GovernanceIntegrityDeliveryEngine(
            self.dispatch_repository,
            self.notification_repository,
            self.channel_repository,
            self.provider_registry,
            self.policy_service,
            self.request_service,
            self.response_service,
        )

        self.history_repository = (
            InMemoryGovernanceIntegrityDeliveryHistoryRepository()
        )

        self.service = GovernanceIntegrityDeliveryHistoryService(
            self.engine, self.history_repository
        )

    def add_notification(self, notification_id: str) -> None:
        self.notification_repository.save(
            GovernanceIntegrityNotification(
                notification_id=notification_id,
                alert_id=f"alert-{notification_id}",
                severity=GovernanceIntegrityAlertSeverity.WARNING,
                message="boom",
                status=GovernanceIntegrityNotificationStatus.PENDING,
                created_at=BASE_TIME,
            )
        )

    def add_channel(
        self,
        name: str,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        self.channel_repository.save(
            GovernanceIntegrityNotificationChannel(
                name=name,
                channel_type=channel_type,
                destination=f"dest-{name}",
                enabled=True,
                created_at=BASE_TIME,
            )
        )

    def add_dispatch(
        self,
        dispatch_id: str,
        *,
        notification_id: str,
        channel_name: str,
    ) -> None:
        self.dispatch_repository.save(
            GovernanceIntegrityNotificationDispatch(
                dispatch_id=dispatch_id,
                notification_id=notification_id,
                channel_name=channel_name,
                status=GovernanceIntegrityDispatchStatus.QUEUED,
                created_at=BASE_TIME,
            )
        )


def make_result(
    dispatch_id: str,
    *,
    status: GovernanceIntegrityDeliveryStatus = (
        GovernanceIntegrityDeliveryStatus.SUCCESS
    ),
    error: str | None = None,
    offset_minutes: int = 0,
) -> GovernanceIntegrityDeliveryResult:
    return GovernanceIntegrityDeliveryResult(
        dispatch_id=dispatch_id,
        channel_name="email",
        status=status,
        delivered_at=BASE_TIME + timedelta(minutes=offset_minutes),
        error=error,
    )


# --- Model -------------------------------------------------------------


def test_record_rejects_empty_delivery_id() -> None:
    with pytest.raises(ValueError, match="delivery_id must not be empty"):
        GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id="  ",
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=BASE_TIME,
            error=None,
        )


def test_record_rejects_naive_delivered_at() -> None:
    with pytest.raises(
        ValueError, match="delivered_at must be timezone-aware"
    ):
        GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id="d1",
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=datetime(2026, 7, 15, 23, 0, 0),
            error=None,
        )


def test_record_rejects_success_with_error() -> None:
    with pytest.raises(
        ValueError, match="error must not be set when status is SUCCESS"
    ):
        GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id="d1",
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=BASE_TIME,
            error="boom",
        )


def test_record_rejects_failed_without_error() -> None:
    with pytest.raises(
        ValueError, match="error must be set when status is FAILED"
    ):
        GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id="d1",
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.FAILED,
            delivered_at=BASE_TIME,
            error=None,
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityDeliveryHistoryRepository()

    record = GovernanceIntegrityDeliveryHistoryRecord(
        delivery_id="d1",
        dispatch_id="d1",
        channel_name="email",
        status=GovernanceIntegrityDeliveryStatus.SUCCESS,
        delivered_at=BASE_TIME,
        error=None,
    )

    repository.save(record)

    assert repository.get("d1") == record


def test_repository_clear_empties_store() -> None:
    repository = InMemoryGovernanceIntegrityDeliveryHistoryRepository()

    repository.save(
        GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id="d1",
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=BASE_TIME,
            error=None,
        )
    )

    repository.clear()

    assert repository.list() == ()


# --- Service: record -----------------------------------------------------


def test_record_success_result() -> None:
    harness = Harness()

    history = harness.service.record(make_result("d1"))

    assert history.status is GovernanceIntegrityDeliveryStatus.SUCCESS


def test_record_failure_result() -> None:
    harness = Harness()

    history = harness.service.record(
        make_result(
            "d1",
            status=GovernanceIntegrityDeliveryStatus.FAILED,
            error="boom",
        )
    )

    assert history.error is not None


def test_record_duplicate_raises_value_error() -> None:
    harness = Harness()

    harness.service.record(make_result("d1"))

    with pytest.raises(ValueError):
        harness.service.record(make_result("d1", offset_minutes=1))


# --- Service: deliver/deliver_all wrappers --------------------------------


def test_deliver_records_history_automatically() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    history = harness.service.deliver("d1")

    assert history.status is GovernanceIntegrityDeliveryStatus.SUCCESS
    assert harness.service.get("d1") == history


def test_deliver_all_records_every_result() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_notification("n2")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )
    harness.add_dispatch(
        "d2", notification_id="n2", channel_name="email"
    )

    histories = harness.service.deliver_all()

    assert len(histories) == 2
    assert set(harness.history_repository.list()) == set(histories)


# --- Service: get/list/clear ---------------------------------------------


def test_list_returns_recorded_history() -> None:
    harness = Harness()

    record = harness.service.record(make_result("d1"))

    assert harness.service.list() == (record,)


def test_get_returns_none_for_missing_record() -> None:
    harness = Harness()

    assert harness.service.get("missing") is None


def test_clear_empties_repository() -> None:
    harness = Harness()

    harness.service.record(make_result("d1"))

    harness.service.clear()

    assert harness.service.list() == ()


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "delivery-history.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityDeliveryHistoryRepository(
        database
    )

    repository.save(
        GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id="d1",
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.FAILED,
            delivered_at=BASE_TIME,
            error="boom",
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityDeliveryHistoryRepository(
            reloaded_database
        )
    )

    record = reloaded_repository.get("d1")

    assert record is not None
    assert record.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert record.error == "boom"


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_history_service_over_sqlite(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "delivery-history-runtime.db"
        )
    )

    runtime.notification_repository.save(
        GovernanceIntegrityNotification(
            notification_id="n1",
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=BASE_TIME,
        )
    )

    channel_service = runtime.build_integrity_notification_channel_service()
    channel_service.create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    runtime.build_integrity_notification_preference_service().create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    dispatcher = runtime.build_integrity_notification_dispatcher()
    dispatches = dispatcher.dispatch_pending()

    history_service = runtime.build_integrity_delivery_history_service()

    history_service.deliver(dispatches[0].dispatch_id)

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "delivery-history-runtime.db"
        )
    )

    reloaded_history_service = (
        reloaded_runtime.build_integrity_delivery_history_service()
    )

    assert len(reloaded_history_service.list()) == 1
