from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, TYPE_CHECKING, runtime_checkable

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)

if TYPE_CHECKING:
    from .deployment_governance_provider_registry import (
        GovernanceIntegrityProviderRegistry,
    )


@dataclass(frozen=True)
class GovernanceIntegrityProviderConfiguration:
    """
    Typed runtime settings for one channel type's delivery provider,
    stored as an immutable string-to-string mapping.
    """

    channel_type: GovernanceIntegrityNotificationChannelType

    values: Mapping[str, str]

    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "values", MappingProxyType(dict(self.values))
        )

        if self.updated_at.tzinfo is None:
            raise ValueError(
                "updated_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_type": self.channel_type.value,
            "values": dict(self.values),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def empty(
        cls,
        channel_type: GovernanceIntegrityNotificationChannelType,
        *,
        checked_at: datetime,
    ) -> "GovernanceIntegrityProviderConfiguration":
        """
        Build the configuration a provider receives when nothing has
        been stored for its channel type.
        """

        return cls(
            channel_type=channel_type,
            values={},
            updated_at=checked_at,
        )


class GovernanceIntegrityProviderConfigurationError(
    RuntimeError
):
    """
    Base error for governance audit provider configuration
    persistence failures.
    """


class GovernanceIntegrityProviderConfigurationAlreadyExistsError(
    GovernanceIntegrityProviderConfigurationError
):
    """
    Raised when a configuration for the same channel type already
    exists.
    """


@runtime_checkable
class GovernanceIntegrityProviderConfigurationRepository(Protocol):
    """
    Persistence contract for per-channel-type governance audit
    provider configuration.
    """

    def save(
        self,
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> GovernanceIntegrityProviderConfiguration:
        """
        Persist one configuration. Raises if a configuration for this
        channel type already exists.
        """

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderConfiguration | None:
        """
        Return one configuration by channel type, or None if it does
        not exist.
        """

    def update(
        self,
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> GovernanceIntegrityProviderConfiguration:
        """
        Replace an existing configuration's stored values. Raises
        KeyError if it does not exist.
        """

    def delete(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        """
        Delete one configuration by channel type. Raises KeyError if
        it does not exist.
        """

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        """
        Return whether a configuration exists for this channel type.
        """

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderConfiguration, ...]:
        """
        Return every configuration, ordered by channel type value.
        """


class InMemoryGovernanceIntegrityProviderConfigurationRepository:
    """
    Thread-safe in-memory implementation of governance audit provider
    configuration storage.
    """

    def __init__(self) -> None:
        self._configurations: dict[
            GovernanceIntegrityNotificationChannelType,
            GovernanceIntegrityProviderConfiguration,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> GovernanceIntegrityProviderConfiguration:
        with self._lock:
            if configuration.channel_type in self._configurations:
                raise (
                    GovernanceIntegrityProviderConfigurationAlreadyExistsError(
                        "provider configuration for channel type "
                        f"'{configuration.channel_type.value}' "
                        "already exists"
                    )
                )

            self._configurations[
                configuration.channel_type
            ] = configuration

            return configuration

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderConfiguration | None:
        with self._lock:
            return self._configurations.get(channel_type)

    def update(
        self,
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> GovernanceIntegrityProviderConfiguration:
        with self._lock:
            if configuration.channel_type not in self._configurations:
                raise KeyError(
                    "provider configuration for channel type "
                    f"'{configuration.channel_type.value}' was not "
                    "found"
                )

            self._configurations[
                configuration.channel_type
            ] = configuration

            return configuration

    def delete(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        with self._lock:
            if channel_type not in self._configurations:
                raise KeyError(
                    "provider configuration for channel type "
                    f"'{channel_type.value}' was not found"
                )

            del self._configurations[channel_type]

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        with self._lock:
            return channel_type in self._configurations

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderConfiguration, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._configurations.values(),
                    key=lambda configuration: (
                        configuration.channel_type.value
                    ),
                )
            )


class GovernanceIntegrityProviderConfigurationService:
    """
    Creates and manages typed runtime settings for governance audit
    delivery providers, and resolves the configuration a delivery
    engine should expose to a provider for a given channel type.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityProviderConfigurationRepository,
        registry: "GovernanceIntegrityProviderRegistry",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._registry = registry

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
        values: Mapping[str, str],
    ) -> GovernanceIntegrityProviderConfiguration:
        """
        Create a new configuration for a channel type.

        Raises LookupError if no provider is registered for this
        channel type, and ValueError if a configuration for this
        channel type already exists.
        """

        if not self._registry.exists(channel_type):
            raise LookupError(
                "no delivery provider registered for channel "
                f"type '{channel_type.value}'"
            )

        if self._repository.exists(channel_type):
            raise ValueError(
                "provider configuration for channel type "
                f"'{channel_type.value}' already exists"
            )

        configuration = GovernanceIntegrityProviderConfiguration(
            channel_type=channel_type,
            values=values,
            updated_at=self._clock(),
        )

        return self._repository.save(configuration)

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderConfiguration | None:
        return self._repository.get(channel_type)

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderConfiguration, ...]:
        return self._repository.list()

    def update(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
        values: Mapping[str, str],
    ) -> GovernanceIntegrityProviderConfiguration:
        """
        Replace an existing configuration's complete set of values.

        Raises KeyError if no configuration exists for this channel
        type.
        """

        if not self._repository.exists(channel_type):
            raise KeyError(
                "provider configuration for channel type "
                f"'{channel_type.value}' was not found"
            )

        updated = GovernanceIntegrityProviderConfiguration(
            channel_type=channel_type,
            values=values,
            updated_at=self._clock(),
        )

        return self._repository.update(updated)

    def delete(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        """
        Delete a configuration by channel type. Raises KeyError if it
        does not exist.
        """

        self._repository.delete(channel_type)

    def resolve(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderConfiguration:
        """
        Return the configured settings for one channel type, or an
        empty configuration if nothing has been stored.
        """

        existing = self._repository.get(channel_type)

        if existing is not None:
            return existing

        return GovernanceIntegrityProviderConfiguration.empty(
            channel_type, checked_at=self._clock()
        )
