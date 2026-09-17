from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    EmailProvider,
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
from backend.observability.deployment_governance_notifications import (
    GovernanceIntegrityNotification,
    GovernanceIntegrityNotificationStatus,
    InMemoryGovernanceIntegrityNotificationRepository,
)
from backend.observability.deployment_governance_persistence import (
    build_deployment_governance_persistence,
)
from backend.observability.deployment_governance_provider_authentication import (
    GovernanceIntegrityProviderAuthenticationService,
)
from backend.observability.deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfigurationService,
    InMemoryGovernanceIntegrityProviderConfigurationRepository,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.deployment_governance_provider_requests import (
    GovernanceIntegrityProviderRequest,
    GovernanceIntegrityProviderRequestService,
)
from backend.observability.deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecretsService,
    InMemoryGovernanceIntegrityProviderSecretsRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


# --- Model ---------------------------------------------------------------


def test_request_mappings_are_immutable() -> None:
    request = GovernanceIntegrityProviderRequest(
        method="POST",
        endpoint="https://example.com/hook",
        headers={"X-API-Key": "abc123"},
        body={"message": "boom"},
        timeout_seconds=30,
    )

    assert isinstance(request.headers, MappingProxyType)
    assert isinstance(request.body, MappingProxyType)

    with pytest.raises(TypeError):
        request.headers["X-API-Key"] = "xyz789"

    with pytest.raises(TypeError):
        request.body["message"] = "tampered"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH"])
def test_request_accepts_allowed_methods(method) -> None:
    request = GovernanceIntegrityProviderRequest(
        method=method,
        endpoint="https://example.com/hook",
        headers={},
        body={},
        timeout_seconds=30,
    )

    assert request.method == method


def test_request_rejects_unsupported_method() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityProviderRequest(
            method="DELETE",
            endpoint="https://example.com/hook",
            headers={},
            body={},
            timeout_seconds=30,
        )


def test_request_rejects_empty_endpoint() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityProviderRequest(
            method="POST",
            endpoint="  ",
            headers={},
            body={},
            timeout_seconds=30,
        )


def test_request_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityProviderRequest(
            method="POST",
            endpoint="https://example.com/hook",
            headers={},
            body={},
            timeout_seconds=0,
        )


def test_request_to_dict() -> None:
    request = GovernanceIntegrityProviderRequest(
        method="POST",
        endpoint="https://example.com/hook",
        headers={"X-API-Key": "abc123"},
        body={"message": "boom"},
        timeout_seconds=30,
    )

    assert request.to_dict() == {
        "method": "POST",
        "endpoint": "https://example.com/hook",
        "headers": {"X-API-Key": "abc123"},
        "body": {"message": "boom"},
        "timeout_seconds": 30,
    }


# --- Service ----------------------------------------------------------


class Harness:
    def __init__(self) -> None:
        self.registry = GovernanceIntegrityProviderRegistry()

        self.registry.register(EMAIL, EmailProvider())
        self.registry.register(WEBHOOK, WebhookProvider())

        self.channel_repository = (
            InMemoryGovernanceIntegrityNotificationChannelRepository()
        )

        self.channel_service = GovernanceIntegrityNotificationChannelService(
            self.channel_repository
        )

        self.configuration_service = (
            GovernanceIntegrityProviderConfigurationService(
                InMemoryGovernanceIntegrityProviderConfigurationRepository(),
                self.registry,
            )
        )

        self.secrets_service = GovernanceIntegrityProviderSecretsService(
            InMemoryGovernanceIntegrityProviderSecretsRepository(),
            self.registry,
        )

        self.authentication_service = (
            GovernanceIntegrityProviderAuthenticationService(
                self.configuration_service,
                self.secrets_service,
                self.registry,
            )
        )

        self.policy_repository = (
            InMemoryGovernanceIntegrityDeliveryPolicyRepository()
        )

        self.policy_service = GovernanceIntegrityDeliveryPolicyService(
            self.policy_repository, self.channel_service
        )

        self.service = GovernanceIntegrityProviderRequestService(
            self.authentication_service,
            self.configuration_service,
            self.policy_service,
            self.registry,
        )

    def add_channel(
        self, name: str, channel_type, destination: str = "dest"
    ) -> GovernanceIntegrityNotificationChannel:
        return self.channel_service.create(name, channel_type, destination)


def _notification(notification_id: str = "n1") -> GovernanceIntegrityNotification:
    return GovernanceIntegrityNotification(
        notification_id=notification_id,
        alert_id=f"alert-{notification_id}",
        severity=GovernanceIntegrityAlertSeverity.WARNING,
        message="boom",
        status=GovernanceIntegrityNotificationStatus.PENDING,
        created_at=BASE_TIME,
    )


def test_build_request_returns_post_method() -> None:
    harness = Harness()
    channel = harness.add_channel("email", EMAIL, "ops@example.com")

    request = harness.service.build(_notification(), channel)

    assert request.method == "POST"


def test_build_request_merges_authentication_headers() -> None:
    harness = Harness()
    channel = harness.add_channel(
        "webhook", WEBHOOK, "https://example.com/hook"
    )

    harness.secrets_service.create(WEBHOOK, {"api_key": "abc123"})

    request = harness.service.build(_notification(), channel)

    assert request.headers["X-API-Key"] == "abc123"


def test_build_request_uses_timeout_from_delivery_policy() -> None:
    harness = Harness()
    channel = harness.add_channel("email", EMAIL, "ops@example.com")

    harness.policy_service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    request = harness.service.build(_notification(), channel)

    assert request.timeout_seconds == 30


def test_build_request_defaults_timeout_when_no_policy_configured() -> None:
    harness = Harness()
    channel = harness.add_channel("email", EMAIL, "ops@example.com")

    request = harness.service.build(_notification(), channel)

    assert request.timeout_seconds == 30


def test_build_raises_when_required_secret_is_missing() -> None:
    harness = Harness()
    channel = harness.add_channel(
        "webhook", WEBHOOK, "https://example.com/hook"
    )

    with pytest.raises(ValueError):
        harness.service.build(_notification(), channel)


def test_build_raises_for_unregistered_channel_type() -> None:
    harness = Harness()

    unregistered_channel = GovernanceIntegrityNotificationChannel(
        name="slack",
        channel_type=GovernanceIntegrityNotificationChannelType.SLACK,
        destination="https://hooks.slack.example/xyz",
        enabled=True,
        created_at=BASE_TIME,
    )

    with pytest.raises(LookupError):
        harness.service.build(_notification(), unregistered_channel)


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_request_service() -> None:
    runtime = build_deployment_governance_persistence()

    runtime.notification_repository.save(_notification())

    channel = (
        runtime.build_integrity_notification_channel_service().create(
            "email", EMAIL, "ops@example.com"
        )
    )

    request = runtime.build_integrity_provider_request_service().build(
        _notification(), channel
    )

    assert request.method == "POST"
    assert request.endpoint == "ops@example.com"
