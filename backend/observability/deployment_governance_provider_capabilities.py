from __future__ import annotations

from dataclasses import dataclass

from .deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
)


@dataclass(frozen=True)
class GovernanceIntegrityProviderCapabilities:
    """
    Feature-support metadata a delivery provider exposes about
    itself, so the delivery engine can validate a channel's delivery
    policy before attempting delivery.
    """

    supports_retry: bool

    supports_timeout: bool

    supports_rate_limit: bool

    supports_attachments: bool

    supports_markdown: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "supports_retry": self.supports_retry,
            "supports_timeout": self.supports_timeout,
            "supports_rate_limit": self.supports_rate_limit,
            "supports_attachments": self.supports_attachments,
            "supports_markdown": self.supports_markdown,
        }


def validate_delivery_policy_capabilities(
    policy: GovernanceIntegrityDeliveryPolicy,
    capabilities: GovernanceIntegrityProviderCapabilities,
) -> None:
    """
    Validate that a resolved delivery policy only relies on features
    its provider's capabilities advertise support for.

    A zero retry_limit means no retries were requested, so
    supports_retry is only required when retry_limit is greater than
    zero. timeout_seconds and rate_limit_per_minute are always
    positive on a configured policy, so supports_timeout and
    supports_rate_limit are always required.

    Raises ValueError if the policy requires an unsupported feature.
    """

    if policy.retry_limit > 0 and not capabilities.supports_retry:
        raise ValueError(
            "delivery policy for channel "
            f"'{policy.channel_name}' requires retry support, which "
            "its registered provider does not support"
        )

    if not capabilities.supports_timeout:
        raise ValueError(
            "delivery policy for channel "
            f"'{policy.channel_name}' requires timeout support, "
            "which its registered provider does not support"
        )

    if not capabilities.supports_rate_limit:
        raise ValueError(
            "delivery policy for channel "
            f"'{policy.channel_name}' requires rate-limit support, "
            "which its registered provider does not support"
        )
