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
from backend.observability.deployment_governance_provider_lifecycle import (
    GovernanceIntegrityProviderMetadata,
    GovernanceIntegrityProviderState,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
SLACK = GovernanceIntegrityNotificationChannelType.SLACK
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


# --- Model ---------------------------------------------------------------


def test_metadata_rejects_naive_registered_at() -> None:
    with pytest.raises(
        ValueError, match="registered_at must be timezone-aware"
    ):
        GovernanceIntegrityProviderMetadata(
            channel_type=EMAIL,
            provider_name="EmailProvider",
            state=GovernanceIntegrityProviderState.ENABLED,
            registered_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_metadata_to_dict() -> None:
    metadata = GovernanceIntegrityProviderMetadata(
        channel_type=EMAIL,
        provider_name="EmailProvider",
        state=GovernanceIntegrityProviderState.DISABLED,
        registered_at=BASE_TIME,
    )

    assert metadata.to_dict() == {
        "channel_type": "email",
        "provider_name": "EmailProvider",
        "state": "disabled",
        "registered_at": BASE_TIME.isoformat(),
    }


# --- Registration starts enabled ------------------------------------------


def test_register_starts_enabled() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    assert registry.metadata(EMAIL).state is (
        GovernanceIntegrityProviderState.ENABLED
    )


# --- Disable ---------------------------------------------------------------


def test_disable_sets_state_to_disabled() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())
    registry.disable(EMAIL)

    assert registry.metadata(EMAIL).state is (
        GovernanceIntegrityProviderState.DISABLED
    )


def test_disable_keeps_provider_registered() -> None:
    """
    A disabled provider stays registered: it remains resolvable, just
    ineligible for delivery.
    """

    registry = GovernanceIntegrityProviderRegistry()

    provider = EmailProvider()
    registry.register(EMAIL, provider)
    registry.disable(EMAIL)

    assert registry.exists(EMAIL)
    assert registry.resolve(EMAIL) is provider


def test_disable_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.disable(EMAIL)


# --- Enable ------------------------------------------------------------


def test_enable_returns_provider_to_enabled() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())
    registry.disable(EMAIL)
    registry.enable(EMAIL)

    assert registry.metadata(EMAIL).state is (
        GovernanceIntegrityProviderState.ENABLED
    )


def test_enable_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.enable(EMAIL)


# --- Replace -----------------------------------------------------------


def test_replace_swaps_resolved_provider() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())

    replacement = EmailProvider()
    registry.replace(EMAIL, replacement)

    assert registry.resolve(EMAIL) is replacement


def test_replace_preserves_registration_timestamp() -> None:
    calls = iter([BASE_TIME, BASE_TIME.replace(hour=1)])

    registry = GovernanceIntegrityProviderRegistry(
        clock=lambda: next(calls)
    )

    registry.register(EMAIL, EmailProvider())
    registry.replace(EMAIL, EmailProvider())

    assert registry.metadata(EMAIL).registered_at == BASE_TIME


def test_replace_preserves_disabled_state() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    registry.register(EMAIL, EmailProvider())
    registry.disable(EMAIL)

    registry.replace(EMAIL, EmailProvider())

    assert registry.metadata(EMAIL).state is (
        GovernanceIntegrityProviderState.DISABLED
    )


def test_replace_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.replace(EMAIL, EmailProvider())


# --- Metadata --------------------------------------------------------------


def test_metadata_raises_for_unregistered_channel_type() -> None:
    registry = GovernanceIntegrityProviderRegistry()

    with pytest.raises(LookupError):
        registry.metadata(EMAIL)


# --- Runtime -------------------------------------------------------------


def test_runtime_default_providers_start_enabled() -> None:
    runtime = build_deployment_governance_persistence()

    registry = runtime.build_integrity_provider_registry()

    for channel_type in (EMAIL, SLACK, WEBHOOK):
        assert registry.metadata(channel_type).state is (
            GovernanceIntegrityProviderState.ENABLED
        )
