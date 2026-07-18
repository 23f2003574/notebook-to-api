from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)


class GovernanceIntegrityProviderState(
    str,
    Enum,
):
    """
    Whether a registered delivery provider is currently allowed to
    deliver dispatches.
    """

    ENABLED = "enabled"

    DISABLED = "disabled"


@dataclass(frozen=True)
class GovernanceIntegrityProviderMetadata:
    """
    Lifecycle metadata tracked alongside a registered delivery
    provider: its current state and when it was first registered.
    """

    channel_type: GovernanceIntegrityNotificationChannelType

    provider_name: str

    state: GovernanceIntegrityProviderState

    registered_at: datetime

    def __post_init__(self) -> None:
        if self.registered_at.tzinfo is None:
            raise ValueError(
                "registered_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_type": self.channel_type.value,
            "provider_name": self.provider_name,
            "state": self.state.value,
            "registered_at": self.registered_at.isoformat(),
        }
