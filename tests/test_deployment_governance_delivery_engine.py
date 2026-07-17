from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    EmailProvider,
    GovernanceIntegrityDeliveryEngine,
    GovernanceIntegrityDeliveryResult,
    GovernanceIntegrityDeliveryStatus,
    SlackProvider,
    WebhookProvider,
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

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

DEFAULT_PROVIDER_REGISTRY = {
    GovernanceIntegrityNotificationChannelType.EMAIL: EmailProvider(),
    GovernanceIntegrityNotificationChannelType.SLACK: SlackProvider(),
    GovernanceIntegrityNotificationChannelType.WEBHOOK: WebhookProvider(),
}


class Harness:
    def __init__(self, *, provider_registry=None, clock=None) -> None:
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

        self.engine = GovernanceIntegrityDeliveryEngine(
            self.dispatch_repository,
            self.notification_repository,
            self.channel_repository,
            (
                DEFAULT_PROVIDER_REGISTRY
                if provider_registry is None
                else provider_registry
            ),
            self.policy_service,
            clock=clock,
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
        offset_minutes: int = 0,
    ) -> None:
        from datetime import timedelta

        self.dispatch_repository.save(
            GovernanceIntegrityNotificationDispatch(
                dispatch_id=dispatch_id,
                notification_id=notification_id,
                channel_name=channel_name,
                status=GovernanceIntegrityDispatchStatus.QUEUED,
                created_at=BASE_TIME + timedelta(minutes=offset_minutes),
            )
        )


# --- Model -------------------------------------------------------------


def test_result_rejects_empty_dispatch_id() -> None:
    with pytest.raises(ValueError, match="dispatch_id must not be empty"):
        GovernanceIntegrityDeliveryResult(
            dispatch_id="  ",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=BASE_TIME,
            error=None,
        )


def test_result_rejects_naive_delivered_at() -> None:
    with pytest.raises(
        ValueError, match="delivered_at must be timezone-aware"
    ):
        GovernanceIntegrityDeliveryResult(
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=datetime(2026, 7, 15, 23, 0, 0),
            error=None,
        )


def test_result_rejects_success_with_error() -> None:
    with pytest.raises(
        ValueError, match="error must not be set when status is SUCCESS"
    ):
        GovernanceIntegrityDeliveryResult(
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=BASE_TIME,
            error="boom",
        )


def test_result_rejects_failed_without_error() -> None:
    with pytest.raises(
        ValueError, match="error must be set when status is FAILED"
    ):
        GovernanceIntegrityDeliveryResult(
            dispatch_id="d1",
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.FAILED,
            delivered_at=BASE_TIME,
            error=None,
        )


# --- Stub providers --------------------------------------------------------


@pytest.mark.parametrize(
    "provider_class",
    [EmailProvider, SlackProvider, WebhookProvider],
)
def test_stub_providers_deliver_without_raising(provider_class) -> None:
    provider = provider_class()

    dispatch = GovernanceIntegrityNotificationDispatch(
        dispatch_id="d1",
        notification_id="n1",
        channel_name="email",
        status=GovernanceIntegrityDispatchStatus.QUEUED,
        created_at=BASE_TIME,
    )
    notification = GovernanceIntegrityNotification(
        notification_id="n1",
        alert_id="alert-1",
        severity=GovernanceIntegrityAlertSeverity.WARNING,
        message="boom",
        status=GovernanceIntegrityNotificationStatus.PENDING,
        created_at=BASE_TIME,
    )
    channel = GovernanceIntegrityNotificationChannel(
        name="email",
        channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
        destination="ops@example.com",
        enabled=True,
        created_at=BASE_TIME,
    )

    assert (
        provider.deliver(dispatch, notification, channel, None) is None
    )


# --- Service: deliver ----------------------------------------------------


def test_deliver_succeeds_with_registered_provider() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.SUCCESS
    assert result.error is None


def test_deliver_succeeds_without_configured_policy() -> None:
    """
    A channel with no delivery policy configured should still deliver
    successfully: policy is optional context passed to the provider,
    not a delivery precondition.
    """

    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.SUCCESS


def test_deliver_fails_when_provider_missing() -> None:
    harness = Harness(provider_registry={})

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert result.error is not None


def test_deliver_fails_when_channel_missing() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="missing-channel"
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED


def test_deliver_fails_when_notification_missing() -> None:
    harness = Harness()

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="missing-notification", channel_name="email"
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED


def test_deliver_raises_for_missing_dispatch() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.engine.deliver("missing")


class RecordingProvider:
    """
    Test double that records the policy it was invoked with, in
    place of a real stub provider.
    """

    def __init__(self) -> None:
        self.received_policies: list[object] = []

    def deliver(self, dispatch, notification, channel, policy):
        self.received_policies.append(policy)


def test_deliver_supplies_resolved_policy_to_provider() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    policy = harness.policy_service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    recording_provider = RecordingProvider()

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        },
        harness.policy_service,
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.SUCCESS
    assert recording_provider.received_policies == [policy]


def test_deliver_supplies_none_policy_when_unconfigured() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    recording_provider = RecordingProvider()

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        },
        harness.policy_service,
    )

    engine.deliver("d1")

    assert recording_provider.received_policies == [None]


# --- Service: deliver_all ---------------------------------------------


def test_deliver_all_processes_every_queued_dispatch() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_notification("n2")
    harness.add_notification("n3")

    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )

    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email", offset_minutes=0
    )
    harness.add_dispatch(
        "d2", notification_id="n2", channel_name="email", offset_minutes=1
    )
    harness.add_dispatch(
        "d3", notification_id="n3", channel_name="email", offset_minutes=2
    )

    results = harness.engine.deliver_all()

    assert len(results) == 3
    assert all(
        result.status is GovernanceIntegrityDeliveryStatus.SUCCESS
        for result in results
    )


def test_deliver_all_returns_empty_when_nothing_queued() -> None:
    harness = Harness()

    assert harness.engine.deliver_all() == ()


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_delivery_engine(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "delivery-runtime.db"
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

    runtime.build_integrity_delivery_policy_service().create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    dispatcher = runtime.build_integrity_notification_dispatcher()
    dispatches = dispatcher.dispatch_pending()

    assert len(dispatches) == 1

    engine = runtime.build_integrity_delivery_engine()

    result = engine.deliver(dispatches[0].dispatch_id)

    assert result.status is GovernanceIntegrityDeliveryStatus.SUCCESS
