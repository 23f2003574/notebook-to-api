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
class GovernanceIntegrityProviderSecrets:
    """
    Sensitive credentials for one channel type's delivery provider,
    stored separately from its typed configuration as an immutable
    string-to-string mapping.

    This is local, unencrypted storage: a production deployment would
    need envelope encryption or an external secrets manager rather
    than storing these values as-is.
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
    ) -> "GovernanceIntegrityProviderSecrets":
        """
        Build the secret set a provider receives when nothing has
        been stored for its channel type.
        """

        return cls(
            channel_type=channel_type,
            values={},
            updated_at=checked_at,
        )


class GovernanceIntegrityProviderSecretsError(
    RuntimeError
):
    """
    Base error for governance audit provider secrets persistence
    failures.
    """


class GovernanceIntegrityProviderSecretsAlreadyExistsError(
    GovernanceIntegrityProviderSecretsError
):
    """
    Raised when a secret set for the same channel type already
    exists.
    """


@runtime_checkable
class GovernanceIntegrityProviderSecretsRepository(Protocol):
    """
    Persistence contract for per-channel-type governance audit
    provider secrets.
    """

    def save(
        self,
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> GovernanceIntegrityProviderSecrets:
        """
        Persist one secret set. Raises if a secret set for this
        channel type already exists.
        """

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderSecrets | None:
        """
        Return one secret set by channel type, or None if it does not
        exist.
        """

    def update(
        self,
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> GovernanceIntegrityProviderSecrets:
        """
        Replace an existing secret set's stored values. Raises
        KeyError if it does not exist.
        """

    def delete(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        """
        Delete one secret set by channel type. Raises KeyError if it
        does not exist.
        """

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        """
        Return whether a secret set exists for this channel type.
        """

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderSecrets, ...]:
        """
        Return every secret set, ordered by channel type value.
        """


class InMemoryGovernanceIntegrityProviderSecretsRepository:
    """
    Thread-safe in-memory implementation of governance audit provider
    secrets storage.
    """

    def __init__(self) -> None:
        self._secrets: dict[
            GovernanceIntegrityNotificationChannelType,
            GovernanceIntegrityProviderSecrets,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> GovernanceIntegrityProviderSecrets:
        with self._lock:
            if secrets.channel_type in self._secrets:
                raise (
                    GovernanceIntegrityProviderSecretsAlreadyExistsError(
                        "provider secrets for channel type "
                        f"'{secrets.channel_type.value}' already "
                        "exist"
                    )
                )

            self._secrets[secrets.channel_type] = secrets

            return secrets

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderSecrets | None:
        with self._lock:
            return self._secrets.get(channel_type)

    def update(
        self,
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> GovernanceIntegrityProviderSecrets:
        with self._lock:
            if secrets.channel_type not in self._secrets:
                raise KeyError(
                    "provider secrets for channel type "
                    f"'{secrets.channel_type.value}' were not found"
                )

            self._secrets[secrets.channel_type] = secrets

            return secrets

    def delete(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        with self._lock:
            if channel_type not in self._secrets:
                raise KeyError(
                    "provider secrets for channel type "
                    f"'{channel_type.value}' were not found"
                )

            del self._secrets[channel_type]

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        with self._lock:
            return channel_type in self._secrets

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderSecrets, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._secrets.values(),
                    key=lambda secrets: secrets.channel_type.value,
                )
            )


class GovernanceIntegrityProviderSecretsService:
    """
    Creates and manages sensitive credentials for governance audit
    delivery providers, and resolves the secret set a delivery engine
    should expose to a provider for a given channel type.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityProviderSecretsRepository,
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
    ) -> GovernanceIntegrityProviderSecrets:
        """
        Create a new secret set for a channel type.

        Raises LookupError if no provider is registered for this
        channel type, and ValueError if a secret set for this channel
        type already exists.
        """

        if not self._registry.exists(channel_type):
            raise LookupError(
                "no delivery provider registered for channel "
                f"type '{channel_type.value}'"
            )

        if self._repository.exists(channel_type):
            raise ValueError(
                "provider secrets for channel type "
                f"'{channel_type.value}' already exist"
            )

        secrets = GovernanceIntegrityProviderSecrets(
            channel_type=channel_type,
            values=values,
            updated_at=self._clock(),
        )

        return self._repository.save(secrets)

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderSecrets | None:
        return self._repository.get(channel_type)

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderSecrets, ...]:
        return self._repository.list()

    def update(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
        values: Mapping[str, str],
    ) -> GovernanceIntegrityProviderSecrets:
        """
        Replace an existing secret set's complete set of values.

        Raises KeyError if no secret set exists for this channel
        type.
        """

        if not self._repository.exists(channel_type):
            raise KeyError(
                "provider secrets for channel type "
                f"'{channel_type.value}' were not found"
            )

        updated = GovernanceIntegrityProviderSecrets(
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
        Delete a secret set by channel type. Raises KeyError if it
        does not exist.
        """

        self._repository.delete(channel_type)

    def resolve(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderSecrets:
        """
        Return the stored secret set for one channel type, or an
        empty secret set if nothing has been stored.
        """

        existing = self._repository.get(channel_type)

        if existing is not None:
            return existing

        return GovernanceIntegrityProviderSecrets.empty(
            channel_type, checked_at=self._clock()
        )
