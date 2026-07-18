from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TYPE_CHECKING

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)

if TYPE_CHECKING:
    from .deployment_governance_provider_configuration import (
        GovernanceIntegrityProviderConfigurationService,
    )
    from .deployment_governance_provider_registry import (
        GovernanceIntegrityProviderRegistry,
    )
    from .deployment_governance_provider_secrets import (
        GovernanceIntegrityProviderSecretsService,
    )


class GovernanceIntegrityAuthenticationType(
    str,
    Enum,
):
    """
    The authentication scheme a delivery provider expects.
    """

    NONE = "none"

    API_KEY = "api_key"

    BEARER_TOKEN = "bearer_token"

    BASIC = "basic"


@dataclass(frozen=True)
class GovernanceIntegrityAuthenticationContext:
    """
    A provider-ready authentication context: request headers and
    parameters built from a channel type's resolved configuration and
    secrets, so a provider never needs raw secrets directly.
    """

    authentication_type: GovernanceIntegrityAuthenticationType

    headers: Mapping[str, str]

    parameters: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "headers", MappingProxyType(dict(self.headers))
        )

        object.__setattr__(
            self, "parameters", MappingProxyType(dict(self.parameters))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "authentication_type": self.authentication_type.value,
            "headers": dict(self.headers),
            "parameters": dict(self.parameters),
        }


class GovernanceIntegrityProviderAuthenticationService:
    """
    Builds a provider-ready authentication context for a channel
    type, from its provider's declared authentication type, resolved
    configuration, and resolved secrets.

    Secrets are only ever read here, never handed to a provider
    directly: providers receive the built context instead.
    """

    def __init__(
        self,
        configuration_service: (
            "GovernanceIntegrityProviderConfigurationService"
        ),
        secrets_service: "GovernanceIntegrityProviderSecretsService",
        registry: "GovernanceIntegrityProviderRegistry",
    ) -> None:
        self._configuration_service = configuration_service

        self._secrets_service = secrets_service

        self._registry = registry

    def build(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityAuthenticationContext:
        """
        Build the authentication context for one channel type.

        Raises LookupError if no provider is registered for this
        channel type, and ValueError if the provider's authentication
        type requires a secret that is not stored.
        """

        provider = self._registry.resolve(channel_type)

        authentication_type = provider.authentication_type()

        # Resolved for parity with the configuration -> secrets ->
        # context build flow; built-in authentication types derive
        # their context from secrets only.
        self._configuration_service.resolve(channel_type)

        secrets = self._secrets_service.resolve(channel_type)

        if authentication_type is GovernanceIntegrityAuthenticationType.NONE:
            return GovernanceIntegrityAuthenticationContext(
                authentication_type=authentication_type,
                headers={},
                parameters={},
            )

        if (
            authentication_type
            is GovernanceIntegrityAuthenticationType.API_KEY
        ):
            api_key = secrets.values.get("api_key")

            if not api_key:
                raise ValueError(
                    f"channel type '{channel_type.value}' requires an "
                    "'api_key' secret for API_KEY authentication, but "
                    "none is stored"
                )

            return GovernanceIntegrityAuthenticationContext(
                authentication_type=authentication_type,
                headers={"X-API-Key": api_key},
                parameters={},
            )

        if (
            authentication_type
            is GovernanceIntegrityAuthenticationType.BEARER_TOKEN
        ):
            token = secrets.values.get("token")

            if not token:
                raise ValueError(
                    f"channel type '{channel_type.value}' requires a "
                    "'token' secret for BEARER_TOKEN authentication, "
                    "but none is stored"
                )

            return GovernanceIntegrityAuthenticationContext(
                authentication_type=authentication_type,
                headers={"Authorization": f"Bearer {token}"},
                parameters={},
            )

        if authentication_type is GovernanceIntegrityAuthenticationType.BASIC:
            username = secrets.values.get("username")
            password = secrets.values.get("password")

            if not username or not password:
                raise ValueError(
                    f"channel type '{channel_type.value}' requires "
                    "'username' and 'password' secrets for BASIC "
                    "authentication, but they are not both stored"
                )

            encoded = base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")

            return GovernanceIntegrityAuthenticationContext(
                authentication_type=authentication_type,
                headers={"Authorization": f"Basic {encoded}"},
                parameters={},
            )

        raise AssertionError(
            "unhandled governance audit provider authentication type "
            f"'{authentication_type}'"
        )
