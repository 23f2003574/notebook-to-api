from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)

if TYPE_CHECKING:
    from .deployment_governance_provider_registry import (
        GovernanceIntegrityProviderRegistry,
    )


class GovernanceIntegrityProviderHealthStatus(
    str,
    Enum,
):
    """
    Operational status of one delivery provider at the time it was
    last checked.
    """

    HEALTHY = "healthy"

    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class GovernanceIntegrityProviderHealth:
    """
    The health of one delivery provider, as of one point-in-time
    check.
    """

    channel_type: GovernanceIntegrityNotificationChannelType

    status: GovernanceIntegrityProviderHealthStatus

    checked_at: datetime

    message: str | None

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError(
                "checked_at must be timezone-aware"
            )

        if self.status is GovernanceIntegrityProviderHealthStatus.HEALTHY:
            if self.message is not None:
                raise ValueError(
                    "message must not be set when status is HEALTHY"
                )

        else:
            if self.message is None:
                raise ValueError(
                    "message must be set when status is UNHEALTHY"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_type": self.channel_type.value,
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "message": self.message,
        }


class GovernanceIntegrityProviderHealthService:
    """
    Read-only service exposing governance audit delivery provider
    health, backed by a provider registry.
    """

    def __init__(
        self,
        registry: "GovernanceIntegrityProviderRegistry",
    ) -> None:
        self._registry = registry

    def check(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderHealth:
        """
        Check the health of the provider registered for a channel
        type.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        return self._registry.health(channel_type)

    def check_all(
        self,
    ) -> tuple[GovernanceIntegrityProviderHealth, ...]:
        """
        Check the health of every registered provider, ordered by
        channel type value.
        """

        return self._registry.health_all()
