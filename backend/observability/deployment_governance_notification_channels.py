from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Protocol, runtime_checkable


class GovernanceIntegrityNotificationChannelType(
    str,
    Enum,
):
    """
    The kind of delivery target a notification channel points at.
    """

    EMAIL = "email"

    WEBHOOK = "webhook"

    SLACK = "slack"


@dataclass(frozen=True)
class GovernanceIntegrityNotificationChannel:
    """
    A named delivery destination for future notification providers.

    No delivery happens yet: a channel only records where a
    notification would be sent.
    """

    name: str

    channel_type: GovernanceIntegrityNotificationChannelType

    destination: str

    enabled: bool

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if not self.destination.strip():
            raise ValueError(
                "destination must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "channel_type": self.channel_type.value,
            "destination": self.destination,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegrityNotificationChannelError(
    RuntimeError
):
    """
    Base error for governance audit notification channel persistence
    failures.
    """


class GovernanceIntegrityNotificationChannelAlreadyExistsError(
    GovernanceIntegrityNotificationChannelError
):
    """
    Raised when a channel with the same name already exists.
    """


@runtime_checkable
class GovernanceIntegrityNotificationChannelRepository(Protocol):
    """
    Persistence contract for named governance audit notification
    channels.
    """

    def save(
        self,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityNotificationChannel:
        """
        Persist one channel. Raises if the name already exists.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationChannel | None:
        """
        Return one channel by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationChannel,
        ...
    ]:
        """
        Return every channel, ordered by name.
        """

    def update(
        self,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityNotificationChannel:
        """
        Replace an existing channel's stored state. Raises KeyError
        if it does not exist.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one channel by name. Raises KeyError if it does not
        exist.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a channel with this name exists.
        """


class InMemoryGovernanceIntegrityNotificationChannelRepository:
    """
    Thread-safe in-memory implementation of governance audit
    notification channel storage.
    """

    def __init__(self) -> None:
        self._channels: dict[
            str,
            GovernanceIntegrityNotificationChannel,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityNotificationChannel:
        with self._lock:
            if channel.name in self._channels:
                raise (
                    GovernanceIntegrityNotificationChannelAlreadyExistsError(
                        f"notification channel '{channel.name}' "
                        "already exists"
                    )
                )

            self._channels[channel.name] = channel

            return channel

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationChannel | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._channels.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationChannel,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._channels.values(),
                    key=lambda channel: channel.name,
                )
            )

    def update(
        self,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityNotificationChannel:
        with self._lock:
            if channel.name not in self._channels:
                raise KeyError(
                    f"notification channel '{channel.name}' "
                    "was not found"
                )

            self._channels[channel.name] = channel

            return channel

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._channels:
                raise KeyError(
                    f"notification channel '{normalized_name}' "
                    "was not found"
                )

            del self._channels[normalized_name]

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._channels

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityNotificationChannelService:
    """
    Creates and manages named governance audit notification delivery
    channels.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityNotificationChannelRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        name: str,
        channel_type: GovernanceIntegrityNotificationChannelType,
        destination: str,
    ) -> GovernanceIntegrityNotificationChannel:
        """
        Create a new, uniquely named channel, enabled by default.

        Raises ValueError if a channel with this name already exists.
        """

        if self._repository.exists(name):
            raise ValueError(
                f"notification channel '{name}' already exists"
            )

        channel = GovernanceIntegrityNotificationChannel(
            name=name,
            channel_type=channel_type,
            destination=destination,
            enabled=True,
            created_at=self._clock(),
        )

        return self._repository.save(channel)

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationChannel | None:
        return self._repository.get(name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationChannel,
        ...
    ]:
        return self._repository.list()

    def enable(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationChannel:
        """
        Enable a channel. Raises KeyError if it does not exist.
        """

        return self._set_enabled(name, True)

    def disable(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationChannel:
        """
        Disable a channel. Raises KeyError if it does not exist.
        """

        return self._set_enabled(name, False)

    def update_destination(
        self,
        name: str,
        destination: str,
    ) -> GovernanceIntegrityNotificationChannel:
        """
        Change a channel's delivery destination. Raises KeyError if
        it does not exist.
        """

        channel = self._repository.get(name)

        if channel is None:
            raise KeyError(
                f"notification channel '{name}' was not found"
            )

        return self._repository.update(
            dataclasses.replace(channel, destination=destination)
        )

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a channel by name. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(name)

    def enabled_channels(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationChannel,
        ...
    ]:
        """
        Return every enabled channel: the current set of resolvable
        delivery targets.
        """

        return tuple(
            channel
            for channel in self._repository.list()
            if channel.enabled
        )

    def _set_enabled(
        self,
        name: str,
        enabled: bool,
    ) -> GovernanceIntegrityNotificationChannel:
        channel = self._repository.get(name)

        if channel is None:
            raise KeyError(
                f"notification channel '{name}' was not found"
            )

        return self._repository.update(
            dataclasses.replace(channel, enabled=enabled)
        )
