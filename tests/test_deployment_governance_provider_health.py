from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    EmailProvider,
    SlackProvider,
    WebhookProvider,
)
from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from backend.observability.deployment_governance_persistence import (
    build_deployment_governance_persistence,
)
from backend.observability.deployment_governance_provider_health import (
    GovernanceIntegrityProviderHealth,
    GovernanceIntegrityProviderHealthStatus,
    GovernanceIntegrityProviderHealthService,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
SLACK = GovernanceIntegrityNotificationChannelType.SLACK
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


# --- Model ---------------------------------------------------------------


def test_healthy_provider_status() -> None:
    health = EmailProvider().health_check()

    assert health.status is GovernanceIntegrityProviderHealthStatus.HEALTHY
    assert health.message is None


@pytest.mark.parametrize(
    "provider_class", [EmailProvider, SlackProvider, WebhookProvider]
)
def test_built_in_providers_default_to_healthy(provider_class) -> None:
    health = provider_class().health_check()

    assert health.status is GovernanceIntegrityProviderHealthStatus.HEALTHY


def test_health_rejects_naive_checked_at() -> None:
    with pytest.raises(ValueError, match="checked_at must be timezone-aware"):
        GovernanceIntegrityProviderHealth(
            channel_type=EMAIL,
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=datetime(2026, 7, 15, 23, 0, 0),
            message=None,
        )


def test_health_rejects_healthy_with_message() -> None:
    with pytest.raises(
        ValueError, match="message must not be set when status is HEALTHY"
    ):
        GovernanceIntegrityProviderHealth(
            channel_type=EMAIL,
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=BASE_TIME,
            message="boom",
        )


def test_health_rejects_unhealthy_without_message() -> None:
    with pytest.raises(
        ValueError, match="message must be set when status is UNHEALTHY"
    ):
        GovernanceIntegrityProviderHealth(
            channel_type=EMAIL,
            status=GovernanceIntegrityProviderHealthStatus.UNHEALTHY,
            checked_at=BASE_TIME,
            message=None,
        )


def test_health_to_dict() -> None:
    health = GovernanceIntegrityProviderHealth(
        channel_type=EMAIL,
        status=GovernanceIntegrityProviderHealthStatus.UNHEALTHY,
        checked_at=BASE_TIME,
        message="offline",
    )

    assert health.to_dict() == {
        "channel_type": "email",
        "status": "unhealthy",
        "checked_at": BASE_TIME.isoformat(),
        "message": "offline",
    }


# --- Registry --------------------------------------------------------------


def test_registry_health_returns_provider_health() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    health = registry.health(EMAIL)

    assert health.status is GovernanceIntegrityProviderHealthStatus.HEALTHY
    assert health.channel_type is EMAIL


def test_registry_health_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.health(WEBHOOK)


def test_registry_health_all_returns_every_registered_provider() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(WEBHOOK, WebhookProvider())
    registry.register(EMAIL, EmailProvider())
    registry.register(SLACK, SlackProvider())

    health_records = registry.health_all()

    assert [health.channel_type for health in health_records] == [
        EMAIL,
        SLACK,
        WEBHOOK,
    ]
    assert all(
        health.status is GovernanceIntegrityProviderHealthStatus.HEALTHY
        for health in health_records
    )


def test_registry_health_all_is_empty_for_new_registry() -> None:
    assert GovernanceIntegrityProviderRegistry().health_all() == ()


# --- Service -----------------------------------------------------------


def test_health_service_check() -> None:
    registry = GovernanceIntegrityProviderRegistry()
    registry.register(EMAIL, EmailProvider())

    service = GovernanceIntegrityProviderHealthService(registry)

    health = service.check(EMAIL)

    assert health.status is GovernanceIntegrityProviderHealthStatus.HEALTHY


def test_health_service_check_raises_for_unregistered_channel_type() -> None:
    service = GovernanceIntegrityProviderHealthService(
        GovernanceIntegrityProviderRegistry()
    )

    with pytest.raises(LookupError):
        service.check(EMAIL)


def test_health_service_check_all() -> None:
    registry = GovernanceIntegrityProviderRegistry()
    registry.register(EMAIL, EmailProvider())
    registry.register(SLACK, SlackProvider())

    service = GovernanceIntegrityProviderHealthService(registry)

    health_records = service.check_all()

    assert len(health_records) == 2


# --- Runtime -------------------------------------------------------------


def test_runtime_default_providers_are_healthy() -> None:
    runtime = build_deployment_governance_persistence()

    service = runtime.build_integrity_provider_health_service()

    health_records = service.check_all()

    assert len(health_records) == 3
    assert all(
        health.status is GovernanceIntegrityProviderHealthStatus.HEALTHY
        for health in health_records
    )
