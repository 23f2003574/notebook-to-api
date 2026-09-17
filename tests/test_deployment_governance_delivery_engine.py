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
from backend.observability.deployment_governance_provider_authentication import (
    GovernanceIntegrityAuthenticationType,
    GovernanceIntegrityProviderAuthenticationService,
)
from backend.observability.deployment_governance_provider_capabilities import (
    GovernanceIntegrityProviderCapabilities,
)
from backend.observability.deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfigurationService,
    InMemoryGovernanceIntegrityProviderConfigurationRepository,
)
from backend.observability.deployment_governance_provider_health import (
    GovernanceIntegrityProviderHealth,
    GovernanceIntegrityProviderHealthStatus,
)
from backend.observability.deployment_governance_provider_lifecycle import (
    GovernanceIntegrityProviderMetadata,
    GovernanceIntegrityProviderState,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.deployment_governance_provider_requests import (
    GovernanceIntegrityProviderRequest,
    GovernanceIntegrityProviderRequestService,
)
from backend.observability.deployment_governance_provider_responses import (
    GovernanceIntegrityProviderResponse,
    GovernanceIntegrityProviderResponseService,
)
from backend.observability.deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecretsService,
    InMemoryGovernanceIntegrityProviderSecretsRepository,
)
from backend.observability.deployment_governance_retry_orchestrator import (
    GovernanceIntegrityRetryOrchestrator,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def _build_provider_registry(providers) -> GovernanceIntegrityProviderRegistry:
    registry = GovernanceIntegrityProviderRegistry()

    for channel_type, provider in providers.items():
        registry.register(channel_type, provider)

    return registry


def _default_provider_registry() -> GovernanceIntegrityProviderRegistry:
    return _build_provider_registry(
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                EmailProvider()
            ),
            GovernanceIntegrityNotificationChannelType.SLACK: (
                SlackProvider()
            ),
            GovernanceIntegrityNotificationChannelType.WEBHOOK: (
                WebhookProvider()
            ),
        }
    )


def _build_request_service(
    registry: GovernanceIntegrityProviderRegistry,
    policy_service: GovernanceIntegrityDeliveryPolicyService,
) -> GovernanceIntegrityProviderRequestService:
    configuration_service = GovernanceIntegrityProviderConfigurationService(
        InMemoryGovernanceIntegrityProviderConfigurationRepository(),
        registry,
    )

    secrets_service = GovernanceIntegrityProviderSecretsService(
        InMemoryGovernanceIntegrityProviderSecretsRepository(),
        registry,
    )

    authentication_service = GovernanceIntegrityProviderAuthenticationService(
        configuration_service, secrets_service, registry
    )

    return GovernanceIntegrityProviderRequestService(
        authentication_service, configuration_service, policy_service, registry
    )


def _build_response_service(
    registry: GovernanceIntegrityProviderRegistry,
) -> GovernanceIntegrityProviderResponseService:
    return GovernanceIntegrityProviderResponseService(registry)


def _build_retry_orchestrator() -> GovernanceIntegrityRetryOrchestrator:
    return GovernanceIntegrityRetryOrchestrator(clock=lambda: BASE_TIME)


class Harness:
    def __init__(
        self,
        *,
        provider_registry=None,
        request_service=None,
        response_service=None,
        retry_orchestrator=None,
        clock=None,
    ) -> None:
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

        self.provider_registry = (
            _default_provider_registry()
            if provider_registry is None
            else provider_registry
        )

        self.request_service = (
            _build_request_service(
                self.provider_registry, self.policy_service
            )
            if request_service is None
            else request_service
        )

        self.response_service = (
            _build_response_service(self.provider_registry)
            if response_service is None
            else response_service
        )

        self.retry_orchestrator = (
            _build_retry_orchestrator()
            if retry_orchestrator is None
            else retry_orchestrator
        )

        self.engine = GovernanceIntegrityDeliveryEngine(
            self.dispatch_repository,
            self.notification_repository,
            self.channel_repository,
            self.provider_registry,
            self.policy_service,
            self.request_service,
            self.response_service,
            self.retry_orchestrator,
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


def _stub_request() -> GovernanceIntegrityProviderRequest:
    return GovernanceIntegrityProviderRequest(
        method="POST",
        endpoint="ops@example.com",
        headers={},
        body={},
        timeout_seconds=30,
    )


@pytest.mark.parametrize(
    "provider_class",
    [EmailProvider, SlackProvider, WebhookProvider],
)
def test_stub_providers_deliver_returns_success_response(provider_class) -> None:
    provider = provider_class()

    response = provider.deliver(_stub_request())

    assert isinstance(response, GovernanceIntegrityProviderResponse)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "provider_class",
    [EmailProvider, SlackProvider, WebhookProvider],
)
def test_stub_providers_build_post_request(provider_class) -> None:
    from backend.observability.deployment_governance_provider_authentication import (
        GovernanceIntegrityAuthenticationContext,
    )
    from backend.observability.deployment_governance_provider_configuration import (
        GovernanceIntegrityProviderConfiguration,
    )

    provider = provider_class()

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
    configuration = GovernanceIntegrityProviderConfiguration.empty(
        GovernanceIntegrityNotificationChannelType.EMAIL,
        checked_at=BASE_TIME,
    )
    authentication = GovernanceIntegrityAuthenticationContext(
        authentication_type=GovernanceIntegrityAuthenticationType.NONE,
        headers={},
        parameters={},
    )

    request = provider.build_request(
        notification, channel, configuration, authentication, None
    )

    assert request.method == "POST"
    assert request.endpoint == "ops@example.com"
    assert request.timeout_seconds == 30


# --- Provider registry integration --------------------------------------


class SpyProviderRegistry:
    """
    Test double recording every channel type resolved through it, in
    place of a real GovernanceIntegrityProviderRegistry.
    """

    def __init__(self, provider) -> None:
        self._provider = provider
        self.resolved_channel_types: list[
            GovernanceIntegrityNotificationChannelType
        ] = []

    def resolve(self, channel_type):
        self.resolved_channel_types.append(channel_type)
        return self._provider

    def health(self, channel_type):
        return GovernanceIntegrityProviderHealth(
            channel_type=channel_type,
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=BASE_TIME,
            message=None,
        )

    def metadata(self, channel_type):
        return GovernanceIntegrityProviderMetadata(
            channel_type=channel_type,
            provider_name=type(self._provider).__name__,
            state=GovernanceIntegrityProviderState.ENABLED,
            registered_at=BASE_TIME,
        )


def test_deliver_requests_provider_through_registry() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    spy_registry = SpyProviderRegistry(EmailProvider())

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        spy_registry,
        harness.policy_service,
        harness.request_service,
        harness.response_service,
        harness.retry_orchestrator,
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.SUCCESS
    assert spy_registry.resolved_channel_types == [
        GovernanceIntegrityNotificationChannelType.EMAIL
    ]


class UnhealthyEmailProvider:
    """
    Test double for a provider whose health check reports UNHEALTHY,
    in place of a real stub provider.
    """

    def __init__(self, message: str = "provider offline") -> None:
        self._message = message

    def deliver(self, request):
        raise AssertionError(
            "deliver must not be called for an unhealthy provider"
        )

    def build_request(
        self, notification, channel, configuration, authentication, policy
    ):
        raise AssertionError(
            "build_request must not be called for an unhealthy provider"
        )

    def capabilities(self):
        return GovernanceIntegrityProviderCapabilities(
            supports_retry=True,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=True,
            supports_markdown=True,
        )

    def health_check(self):
        return GovernanceIntegrityProviderHealth(
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            status=GovernanceIntegrityProviderHealthStatus.UNHEALTHY,
            checked_at=BASE_TIME,
            message=self._message,
        )

    def authentication_type(self):
        return GovernanceIntegrityAuthenticationType.NONE


def test_deliver_fails_when_provider_is_unhealthy() -> None:
    harness = Harness(
        provider_registry=_build_provider_registry(
            {
                GovernanceIntegrityNotificationChannelType.EMAIL: (
                    UnhealthyEmailProvider("mailbox unreachable")
                ),
            }
        )
    )

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert "mailbox unreachable" in result.error


def test_deliver_fails_when_provider_is_disabled() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    harness.provider_registry.disable(
        GovernanceIntegrityNotificationChannelType.EMAIL
    )

    result = harness.engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert result.error == "Provider is disabled."


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
    successfully: policy is optional context passed through to the
    request builder, not a delivery precondition.
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
    harness = Harness(
        provider_registry=GovernanceIntegrityProviderRegistry()
    )

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
    Test double that records every request it was asked to deliver,
    in place of a real stub provider. build_request() delegates to
    the shared stub builder so its output stays realistic.
    """

    def __init__(
        self,
        authentication_type=GovernanceIntegrityAuthenticationType.NONE,
        response_status_code: int = 200,
    ) -> None:
        self.received_requests: list[GovernanceIntegrityProviderRequest] = []
        self.deliver_call_count = 0
        self._authentication_type = authentication_type
        self._response_status_code = response_status_code

    def deliver(self, request):
        self.deliver_call_count += 1
        self.received_requests.append(request)

        return GovernanceIntegrityProviderResponse(
            status_code=self._response_status_code,
            headers={},
            body={},
            duration_ms=0,
        )

    def build_request(
        self, notification, channel, configuration, authentication, policy
    ):
        headers = dict(authentication.headers)

        return GovernanceIntegrityProviderRequest(
            method="POST",
            endpoint=channel.destination,
            headers=headers,
            body={"notification_id": notification.notification_id},
            timeout_seconds=(
                policy.timeout_seconds if policy is not None else 30
            ),
        )

    def capabilities(self):
        return GovernanceIntegrityProviderCapabilities(
            supports_retry=True,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=True,
            supports_markdown=True,
        )

    def health_check(self):
        return GovernanceIntegrityProviderHealth(
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=BASE_TIME,
            message=None,
        )

    def authentication_type(self):
        return self._authentication_type


def test_deliver_calls_provider_deliver_with_built_request_exactly_once() -> (
    None
):
    """
    The delivery engine must call provider.deliver(request) exactly
    once, passing the request the pipeline built rather than
    assembling provider inputs itself.
    """

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
        timeout_seconds=45,
        rate_limit_per_minute=60,
    )

    recording_provider = RecordingProvider()

    custom_registry = _build_provider_registry(
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        }
    )

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        custom_registry,
        harness.policy_service,
        _build_request_service(custom_registry, harness.policy_service),
        _build_response_service(custom_registry),
        _build_retry_orchestrator(),
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.SUCCESS
    assert recording_provider.deliver_call_count == 1
    assert len(recording_provider.received_requests) == 1

    request = recording_provider.received_requests[0]
    assert isinstance(request, GovernanceIntegrityProviderRequest)
    assert request.endpoint == "dest-email"
    assert request.timeout_seconds == policy.timeout_seconds


def test_deliver_does_not_call_provider_deliver_when_request_fails_to_build() -> (
    None
):
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    recording_provider = RecordingProvider(
        authentication_type=GovernanceIntegrityAuthenticationType.API_KEY
    )

    custom_registry = _build_provider_registry(
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        }
    )

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        custom_registry,
        harness.policy_service,
        _build_request_service(custom_registry, harness.policy_service),
        _build_response_service(custom_registry),
        _build_retry_orchestrator(),
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert "api_key" in result.error
    assert recording_provider.deliver_call_count == 0


def test_deliver_fails_on_normalized_server_error_response() -> None:
    """
    A provider that returns a 5xx response should produce a FAILED
    delivery result: the engine relies on the response service's
    normalized outcome rather than interpreting the raw response
    itself.
    """

    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    recording_provider = RecordingProvider(response_status_code=503)

    custom_registry = _build_provider_registry(
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        }
    )

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        custom_registry,
        harness.policy_service,
        _build_request_service(custom_registry, harness.policy_service),
        _build_response_service(custom_registry),
        _build_retry_orchestrator(),
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert "server error" in result.error
    assert recording_provider.deliver_call_count == 1


def test_deliver_fails_on_normalized_client_error_response() -> None:
    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    recording_provider = RecordingProvider(response_status_code=404)

    custom_registry = _build_provider_registry(
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        }
    )

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        custom_registry,
        harness.policy_service,
        _build_request_service(custom_registry, harness.policy_service),
        _build_response_service(custom_registry),
        _build_retry_orchestrator(),
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert "client error" in result.error


def test_deliver_raises_on_unsupported_response_status_code() -> None:
    """
    An unsupported status code cannot be normalized by the response
    service, so the delivery engine surfaces it as a FAILED result
    (the ValueError is caught alongside every other delivery
    failure).
    """

    harness = Harness()

    harness.add_notification("n1")
    harness.add_channel(
        "email", GovernanceIntegrityNotificationChannelType.EMAIL
    )
    harness.add_dispatch(
        "d1", notification_id="n1", channel_name="email"
    )

    recording_provider = RecordingProvider(response_status_code=999)

    custom_registry = _build_provider_registry(
        {
            GovernanceIntegrityNotificationChannelType.EMAIL: (
                recording_provider
            ),
        }
    )

    engine = GovernanceIntegrityDeliveryEngine(
        harness.dispatch_repository,
        harness.notification_repository,
        harness.channel_repository,
        custom_registry,
        harness.policy_service,
        _build_request_service(custom_registry, harness.policy_service),
        _build_response_service(custom_registry),
        _build_retry_orchestrator(),
    )

    result = engine.deliver("d1")

    assert result.status is GovernanceIntegrityDeliveryStatus.FAILED
    assert "999" in result.error


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
