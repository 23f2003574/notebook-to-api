from __future__ import annotations

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    EmailProvider,
    SlackProvider,
    WebhookProvider,
)
from backend.observability.deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
)
from backend.observability.deployment_governance_provider_capabilities import (
    GovernanceIntegrityProviderCapabilities,
    validate_delivery_policy_capabilities,
)


# --- Capabilities ----------------------------------------------------------


def test_email_provider_capabilities() -> None:
    caps = EmailProvider().capabilities()

    assert caps.supports_retry
    assert caps.supports_timeout
    assert caps.supports_rate_limit
    assert caps.supports_attachments
    assert not caps.supports_markdown


def test_slack_provider_capabilities() -> None:
    caps = SlackProvider().capabilities()

    assert caps.supports_retry
    assert caps.supports_timeout
    assert caps.supports_rate_limit
    assert caps.supports_attachments
    assert caps.supports_markdown


def test_webhook_provider_capabilities() -> None:
    caps = WebhookProvider().capabilities()

    assert caps.supports_retry
    assert caps.supports_timeout
    assert caps.supports_rate_limit
    assert not caps.supports_attachments
    assert not caps.supports_markdown


def test_capabilities_to_dict() -> None:
    caps = GovernanceIntegrityProviderCapabilities(
        supports_retry=True,
        supports_timeout=False,
        supports_rate_limit=True,
        supports_attachments=False,
        supports_markdown=True,
    )

    assert caps.to_dict() == {
        "supports_retry": True,
        "supports_timeout": False,
        "supports_rate_limit": True,
        "supports_attachments": False,
        "supports_markdown": True,
    }


# --- Validation --------------------------------------------------------


def _policy(
    *,
    retry_limit: int = 3,
    timeout_seconds: int = 30,
    rate_limit_per_minute: int = 60,
) -> GovernanceIntegrityDeliveryPolicy:
    return GovernanceIntegrityDeliveryPolicy(
        channel_name="email",
        retry_limit=retry_limit,
        timeout_seconds=timeout_seconds,
        rate_limit_per_minute=rate_limit_per_minute,
        enabled=True,
    )


def test_validate_passes_when_capabilities_support_policy() -> None:
    validate_delivery_policy_capabilities(
        _policy(),
        GovernanceIntegrityProviderCapabilities(
            supports_retry=True,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=True,
            supports_markdown=True,
        ),
    )


def test_validate_ignores_retry_support_when_retry_limit_is_zero() -> None:
    validate_delivery_policy_capabilities(
        _policy(retry_limit=0),
        GovernanceIntegrityProviderCapabilities(
            supports_retry=False,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=False,
            supports_markdown=False,
        ),
    )


def test_validate_rejects_unsupported_retry() -> None:
    with pytest.raises(ValueError, match="retry support"):
        validate_delivery_policy_capabilities(
            _policy(retry_limit=3),
            GovernanceIntegrityProviderCapabilities(
                supports_retry=False,
                supports_timeout=True,
                supports_rate_limit=True,
                supports_attachments=False,
                supports_markdown=False,
            ),
        )


def test_validate_rejects_unsupported_timeout() -> None:
    """
    A mock provider that does not support timeout should fail
    validation against a policy that always configures a timeout.
    """

    with pytest.raises(ValueError, match="timeout support"):
        validate_delivery_policy_capabilities(
            _policy(timeout_seconds=30),
            GovernanceIntegrityProviderCapabilities(
                supports_retry=True,
                supports_timeout=False,
                supports_rate_limit=True,
                supports_attachments=False,
                supports_markdown=False,
            ),
        )


def test_validate_rejects_unsupported_rate_limit() -> None:
    with pytest.raises(ValueError, match="rate-limit support"):
        validate_delivery_policy_capabilities(
            _policy(),
            GovernanceIntegrityProviderCapabilities(
                supports_retry=True,
                supports_timeout=True,
                supports_rate_limit=False,
                supports_attachments=False,
                supports_markdown=False,
            ),
        )
