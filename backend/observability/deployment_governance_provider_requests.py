from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
)
from .deployment_governance_notifications import (
    GovernanceIntegrityNotification,
)

if TYPE_CHECKING:
    from .deployment_governance_delivery_policies import (
        GovernanceIntegrityDeliveryPolicyService,
    )
    from .deployment_governance_provider_authentication import (
        GovernanceIntegrityProviderAuthenticationService,
    )
    from .deployment_governance_provider_configuration import (
        GovernanceIntegrityProviderConfigurationService,
    )
    from .deployment_governance_provider_registry import (
        GovernanceIntegrityProviderRegistry,
    )

_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH"})


@dataclass(frozen=True)
class GovernanceIntegrityProviderRequest:
    """
    A provider-ready request: everything a provider needs to attempt
    delivery, built once by the request pipeline instead of assembled
    piecemeal by each provider.
    """

    method: str

    endpoint: str

    headers: Mapping[str, str]

    body: Mapping[str, Any]

    timeout_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "headers", MappingProxyType(dict(self.headers))
        )

        object.__setattr__(
            self, "body", MappingProxyType(dict(self.body))
        )

        if self.method not in _ALLOWED_METHODS:
            raise ValueError(
                f"method must be one of {sorted(_ALLOWED_METHODS)}, "
                f"got '{self.method}'"
            )

        if not self.endpoint.strip():
            raise ValueError(
                "endpoint must not be empty"
            )

        if self.timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "endpoint": self.endpoint,
            "headers": dict(self.headers),
            "body": dict(self.body),
            "timeout_seconds": self.timeout_seconds,
        }


class GovernanceIntegrityProviderRequestService:
    """
    Converts one notification and channel into a provider-ready
    request: resolves configuration, authentication, and delivery
    policy, then delegates the actual request shape to the
    resolved provider's build_request().
    """

    def __init__(
        self,
        authentication_service: (
            "GovernanceIntegrityProviderAuthenticationService"
        ),
        configuration_service: (
            "GovernanceIntegrityProviderConfigurationService"
        ),
        policy_service: "GovernanceIntegrityDeliveryPolicyService",
        registry: "GovernanceIntegrityProviderRegistry",
    ) -> None:
        self._authentication_service = authentication_service

        self._configuration_service = configuration_service

        self._policy_service = policy_service

        self._registry = registry

    def build(
        self,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityProviderRequest:
        """
        Build the provider-ready request for delivering one
        notification through one channel.

        Raises LookupError if no provider is registered for the
        channel's type, and ValueError if the provider's
        authentication type requires a secret that is not stored.
        """

        provider = self._registry.resolve(channel.channel_type)

        configuration = self._configuration_service.resolve(
            channel.channel_type
        )

        authentication = self._authentication_service.build(
            channel.channel_type
        )

        try:
            policy = self._policy_service.resolve(channel.name)

        except LookupError:
            policy = None

        return provider.build_request(
            notification, channel, configuration, authentication, policy
        )
