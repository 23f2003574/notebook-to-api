from __future__ import annotations

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
from backend.observability.deployment_governance_provider_lifecycle import (
    GovernanceIntegrityProviderState,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistration,
    GovernanceIntegrityProviderRegistry,
)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
SLACK = GovernanceIntegrityNotificationChannelType.SLACK
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


# --- Model ---------------------------------------------------------------


def test_registration_to_dict() -> None:
    registration = GovernanceIntegrityProviderRegistration(
        channel_type=EMAIL,
        provider_name="EmailProvider",
    )

    assert registration.to_dict() == {
        "channel_type": "email",
        "provider_name": "EmailProvider",
    }


# --- Register --------------------------------------------------------------


def test_register_makes_channel_type_resolvable() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    assert registry.exists(EMAIL)


def test_register_rejects_duplicate_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    with pytest.raises(ValueError):
        registry.register(EMAIL, EmailProvider())


def test_register_returns_registration_with_provider_name() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registration = registry.register(SLACK, SlackProvider())

    assert registration.channel_type is SLACK
    assert registration.provider_name == "SlackProvider"


# --- Capabilities --------------------------------------------------------


def test_capabilities_returns_provider_metadata() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    caps = registry.capabilities(EMAIL)

    assert caps.supports_retry
    assert caps.supports_timeout
    assert caps.supports_rate_limit


def test_capabilities_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.capabilities(WEBHOOK)


# --- Health --------------------------------------------------------------


def test_health_returns_provider_status() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    assert registry.health(EMAIL).status.value == "healthy"


def test_health_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.health(WEBHOOK)


def test_health_all_returns_every_registered_provider() -> None:
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


# --- Lifecycle -------------------------------------------------------------


def test_disable_reflects_in_metadata() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())
    registry.disable(EMAIL)

    assert registry.metadata(EMAIL).state is (
        GovernanceIntegrityProviderState.DISABLED
    )


def test_replace_returns_resolvable_replacement() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    replacement = EmailProvider()
    registry.replace(EMAIL, replacement)

    assert registry.resolve(EMAIL) is replacement


def test_metadata_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.metadata(WEBHOOK)


# --- Resolve -----------------------------------------------------------


def test_resolve_returns_registered_provider_instance() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    provider = EmailProvider()

    registry.register(EMAIL, provider)

    assert registry.resolve(EMAIL) is provider


def test_resolve_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.resolve(WEBHOOK)


# --- Unregister --------------------------------------------------------


def test_unregister_removes_provider() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    registry.unregister(EMAIL)

    assert not registry.exists(EMAIL)

    with pytest.raises(LookupError):
        registry.resolve(EMAIL)


def test_unregister_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(KeyError):
        registry.unregister(EMAIL)


def test_unregister_allows_re_registration() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())
    registry.unregister(EMAIL)

    new_provider = EmailProvider()

    registry.register(EMAIL, new_provider)

    assert registry.resolve(EMAIL) is new_provider


# --- List / exists -------------------------------------------------------


def test_list_returns_every_registration_ordered_by_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(WEBHOOK, WebhookProvider())
    registry.register(EMAIL, EmailProvider())
    registry.register(SLACK, SlackProvider())

    registrations = registry.list()

    assert [
        registration.channel_type for registration in registrations
    ] == [EMAIL, SLACK, WEBHOOK]


def test_list_is_empty_for_new_registry() -> None:
    assert GovernanceIntegrityProviderRegistry().list() == ()


def test_exists_is_false_for_unregistered_channel_type() -> None:
    assert not GovernanceIntegrityProviderRegistry().exists(EMAIL)


# --- Runtime -------------------------------------------------------------


def test_runtime_registers_every_default_provider() -> None:
    runtime = build_deployment_governance_persistence()

    registry = runtime.build_integrity_provider_registry()

    assert registry.exists(EMAIL)
    assert registry.exists(SLACK)
    assert registry.exists(WEBHOOK)

    assert isinstance(registry.resolve(EMAIL), EmailProvider)
    assert isinstance(registry.resolve(SLACK), SlackProvider)
    assert isinstance(registry.resolve(WEBHOOK), WebhookProvider)

    for channel_type in (EMAIL, SLACK, WEBHOOK):
        caps = registry.capabilities(channel_type)
        assert caps.supports_retry
        assert caps.supports_timeout
        assert caps.supports_rate_limit

        assert registry.health(channel_type).status.value == "healthy"

        assert registry.metadata(channel_type).state is (
            GovernanceIntegrityProviderState.ENABLED
        )
