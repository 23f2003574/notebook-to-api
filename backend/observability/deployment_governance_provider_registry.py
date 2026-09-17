from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, TYPE_CHECKING

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from .deployment_governance_provider_lifecycle import (
    GovernanceIntegrityProviderMetadata,
    GovernanceIntegrityProviderState,
)

if TYPE_CHECKING:
    from .deployment_governance_delivery_engine import (
        GovernanceIntegrityNotificationProvider,
    )
    from .deployment_governance_provider_capabilities import (
        GovernanceIntegrityProviderCapabilities,
    )
    from .deployment_governance_provider_health import (
        GovernanceIntegrityProviderHealth,
    )


@dataclass(frozen=True)
class GovernanceIntegrityProviderRegistration:
    """
    Metadata describing which provider is registered for a channel
    type.
    """

    channel_type: GovernanceIntegrityNotificationChannelType

    provider_name: str

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_type": self.channel_type.value,
            "provider_name": self.provider_name,
        }


class GovernanceIntegrityProviderRegistry:
    """
    Central lookup of governance audit notification delivery
    providers by channel type.

    Exactly one provider may be registered per channel type. The
    delivery engine resolves providers exclusively through this
    registry: it holds no provider-specific logic of its own.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._providers: dict[
            GovernanceIntegrityNotificationChannelType,
            "GovernanceIntegrityNotificationProvider",
        ] = {}

        self._registrations: dict[
            GovernanceIntegrityNotificationChannelType,
            GovernanceIntegrityProviderRegistration,
        ] = {}

        self._metadata: dict[
            GovernanceIntegrityNotificationChannelType,
            GovernanceIntegrityProviderMetadata,
        ] = {}

        self._lock = RLock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def register(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
        provider: "GovernanceIntegrityNotificationProvider",
    ) -> GovernanceIntegrityProviderRegistration:
        """
        Register a provider for a channel type, enabled by default.

        Raises ValueError if a provider is already registered for
        this channel type.
        """

        with self._lock:
            if channel_type in self._providers:
                raise ValueError(
                    "a delivery provider is already registered for "
                    f"channel type '{channel_type.value}'"
                )

            registration = GovernanceIntegrityProviderRegistration(
                channel_type=channel_type,
                provider_name=type(provider).__name__,
            )

            self._providers[channel_type] = provider

            self._registrations[channel_type] = registration

            self._metadata[channel_type] = (
                GovernanceIntegrityProviderMetadata(
                    channel_type=channel_type,
                    provider_name=registration.provider_name,
                    state=GovernanceIntegrityProviderState.ENABLED,
                    registered_at=self._clock(),
                )
            )

            return registration

    def resolve(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> "GovernanceIntegrityNotificationProvider":
        """
        Return the provider registered for a channel type.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        with self._lock:
            provider = self._providers.get(channel_type)

            if provider is None:
                raise LookupError(
                    "no delivery provider registered for channel "
                    f"type '{channel_type.value}'"
                )

            return provider

    def capabilities(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> "GovernanceIntegrityProviderCapabilities":
        """
        Return the capabilities of the provider registered for a
        channel type.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        return self.resolve(channel_type).capabilities()

    def health(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> "GovernanceIntegrityProviderHealth":
        """
        Return the health of the provider registered for a channel
        type.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        return self.resolve(channel_type).health_check()

    def health_all(
        self,
    ) -> tuple["GovernanceIntegrityProviderHealth", ...]:
        """
        Return the health of every registered provider, ordered by
        channel type value.
        """

        return tuple(
            self.resolve(registration.channel_type).health_check()
            for registration in self.list()
        )

    def metadata(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderMetadata:
        """
        Return the lifecycle metadata of the provider registered for
        a channel type.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        with self._lock:
            metadata = self._metadata.get(channel_type)

            if metadata is None:
                raise LookupError(
                    "no delivery provider registered for channel "
                    f"type '{channel_type.value}'"
                )

            return metadata

    def enable(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderMetadata:
        """
        Enable the provider registered for a channel type.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        return self._set_state(
            channel_type, GovernanceIntegrityProviderState.ENABLED
        )

    def disable(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderMetadata:
        """
        Disable the provider registered for a channel type. A
        disabled provider remains registered: it stays resolvable and
        keeps its metadata, but the delivery engine refuses to
        deliver through it.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        return self._set_state(
            channel_type, GovernanceIntegrityProviderState.DISABLED
        )

    def replace(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
        provider: "GovernanceIntegrityNotificationProvider",
    ) -> GovernanceIntegrityProviderRegistration:
        """
        Replace the provider registered for a channel type with a
        different provider instance, preserving its registration
        timestamp and current lifecycle state.

        Raises LookupError if no provider is registered for this
        channel type.
        """

        with self._lock:
            existing_metadata = self._metadata.get(channel_type)

            if existing_metadata is None:
                raise LookupError(
                    "no delivery provider registered for channel "
                    f"type '{channel_type.value}'"
                )

            registration = GovernanceIntegrityProviderRegistration(
                channel_type=channel_type,
                provider_name=type(provider).__name__,
            )

            self._providers[channel_type] = provider

            self._registrations[channel_type] = registration

            self._metadata[channel_type] = dataclasses.replace(
                existing_metadata,
                provider_name=registration.provider_name,
            )

            return registration

    def _set_state(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
        state: GovernanceIntegrityProviderState,
    ) -> GovernanceIntegrityProviderMetadata:
        with self._lock:
            existing_metadata = self._metadata.get(channel_type)

            if existing_metadata is None:
                raise LookupError(
                    "no delivery provider registered for channel "
                    f"type '{channel_type.value}'"
                )

            updated = dataclasses.replace(
                existing_metadata, state=state
            )

            self._metadata[channel_type] = updated

            return updated

    def unregister(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        """
        Remove the provider registered for a channel type.

        Raises KeyError if no provider is registered for this channel
        type.
        """

        with self._lock:
            if channel_type not in self._providers:
                raise KeyError(
                    "no delivery provider registered for channel "
                    f"type '{channel_type.value}'"
                )

            del self._providers[channel_type]

            del self._registrations[channel_type]

            del self._metadata[channel_type]

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderRegistration, ...]:
        """
        Return every registration, ordered by channel type value.
        """

        with self._lock:
            return tuple(
                sorted(
                    self._registrations.values(),
                    key=lambda registration: (
                        registration.channel_type.value
                    ),
                )
            )

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        """
        Return whether a provider is registered for a channel type.
        """

        with self._lock:
            return channel_type in self._providers
