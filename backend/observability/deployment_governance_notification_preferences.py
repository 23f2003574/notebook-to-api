from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable
from threading import RLock

from .deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertSeverity,
)
from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
    GovernanceIntegrityNotificationChannelService,
)

_SEVERITY_RANK: dict[GovernanceIntegrityAlertSeverity, int] = {
    GovernanceIntegrityAlertSeverity.INFO: 0,
    GovernanceIntegrityAlertSeverity.WARNING: 1,
    GovernanceIntegrityAlertSeverity.CRITICAL: 2,
}


@dataclass(frozen=True)
class GovernanceIntegrityNotificationPreference:
    """
    A named routing rule: which channels a notification should reach
    once its severity meets a minimum threshold.
    """

    name: str

    minimum_severity: GovernanceIntegrityAlertSeverity

    channels: tuple[str, ...]

    enabled: bool

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if not self.channels:
            raise ValueError(
                "channels must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "minimum_severity": self.minimum_severity.value,
            "channels": list(self.channels),
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


class GovernanceIntegrityNotificationPreferenceError(
    RuntimeError
):
    """
    Base error for governance audit notification preference
    persistence failures.
    """


class GovernanceIntegrityNotificationPreferenceAlreadyExistsError(
    GovernanceIntegrityNotificationPreferenceError
):
    """
    Raised when a preference with the same name already exists.
    """


@runtime_checkable
class GovernanceIntegrityNotificationPreferenceRepository(Protocol):
    """
    Persistence contract for named governance audit notification
    routing preferences.
    """

    def save(
        self,
        preference: GovernanceIntegrityNotificationPreference,
    ) -> GovernanceIntegrityNotificationPreference:
        """
        Persist one preference. Raises if the name already exists.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationPreference | None:
        """
        Return one preference by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationPreference,
        ...
    ]:
        """
        Return every preference, ordered by name.
        """

    def update(
        self,
        preference: GovernanceIntegrityNotificationPreference,
    ) -> GovernanceIntegrityNotificationPreference:
        """
        Replace an existing preference's stored state. Raises
        KeyError if it does not exist.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one preference by name. Raises KeyError if it does not
        exist.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a preference with this name exists.
        """


class InMemoryGovernanceIntegrityNotificationPreferenceRepository:
    """
    Thread-safe in-memory implementation of governance audit
    notification preference storage.
    """

    def __init__(self) -> None:
        self._preferences: dict[
            str,
            GovernanceIntegrityNotificationPreference,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        preference: GovernanceIntegrityNotificationPreference,
    ) -> GovernanceIntegrityNotificationPreference:
        with self._lock:
            if preference.name in self._preferences:
                raise (
                    GovernanceIntegrityNotificationPreferenceAlreadyExistsError(
                        f"notification preference '{preference.name}' "
                        "already exists"
                    )
                )

            self._preferences[preference.name] = preference

            return preference

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationPreference | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._preferences.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationPreference,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._preferences.values(),
                    key=lambda preference: preference.name,
                )
            )

    def update(
        self,
        preference: GovernanceIntegrityNotificationPreference,
    ) -> GovernanceIntegrityNotificationPreference:
        with self._lock:
            if preference.name not in self._preferences:
                raise KeyError(
                    f"notification preference '{preference.name}' "
                    "was not found"
                )

            self._preferences[preference.name] = preference

            return preference

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._preferences:
                raise KeyError(
                    f"notification preference '{normalized_name}' "
                    "was not found"
                )

            del self._preferences[normalized_name]

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._preferences

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityNotificationPreferenceService:
    """
    Creates and manages named governance audit notification routing
    preferences, and resolves which channels a given severity should
    reach.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityNotificationPreferenceRepository,
        channel_service: GovernanceIntegrityNotificationChannelService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._channel_service = channel_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def create(
        self,
        name: str,
        minimum_severity: GovernanceIntegrityAlertSeverity,
        channels: tuple[str, ...],
    ) -> GovernanceIntegrityNotificationPreference:
        """
        Create a new, uniquely named preference, enabled by default.

        Raises ValueError if a preference with this name already
        exists.
        """

        if self._repository.exists(name):
            raise ValueError(
                f"notification preference '{name}' already exists"
            )

        preference = GovernanceIntegrityNotificationPreference(
            name=name,
            minimum_severity=minimum_severity,
            channels=channels,
            enabled=True,
            created_at=self._clock(),
        )

        return self._repository.save(preference)

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationPreference | None:
        return self._repository.get(name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationPreference,
        ...
    ]:
        return self._repository.list()

    def update(
        self,
        name: str,
        *,
        minimum_severity: GovernanceIntegrityAlertSeverity | None = None,
        channels: tuple[str, ...] | None = None,
        enabled: bool | None = None,
    ) -> GovernanceIntegrityNotificationPreference:
        """
        Update an existing preference's severity threshold, channel
        list, and/or enabled state.

        Fields left as None keep their current value. Raises
        KeyError if no preference with this name exists.
        """

        existing = self._repository.get(name)

        if existing is None:
            raise KeyError(
                f"notification preference '{name}' was not found"
            )

        updated = dataclasses.replace(
            existing,
            minimum_severity=(
                existing.minimum_severity
                if minimum_severity is None
                else minimum_severity
            ),
            channels=(
                existing.channels
                if channels is None
                else channels
            ),
            enabled=(
                existing.enabled
                if enabled is None
                else enabled
            ),
        )

        return self._repository.update(updated)

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a preference by name. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(name)

    def resolve(
        self,
        severity: GovernanceIntegrityAlertSeverity,
    ) -> tuple[
        GovernanceIntegrityNotificationChannel,
        ...
    ]:
        """
        Return every enabled channel reachable by a notification of
        this severity, according to every enabled preference whose
        minimum_severity this severity meets or exceeds.

        Channel order follows first appearance across preferences
        (ordered by name); each channel appears at most once.
        """

        matching_channel_names: list[str] = []

        seen_channel_names: set[str] = set()

        for preference in self._repository.list():
            if not preference.enabled:
                continue

            if (
                _SEVERITY_RANK[severity]
                < _SEVERITY_RANK[preference.minimum_severity]
            ):
                continue

            for channel_name in preference.channels:
                if channel_name in seen_channel_names:
                    continue

                seen_channel_names.add(channel_name)

                matching_channel_names.append(channel_name)

        enabled_channels_by_name = {
            channel.name: channel
            for channel in self._channel_service.enabled_channels()
        }

        return tuple(
            enabled_channels_by_name[channel_name]
            for channel_name in matching_channel_names
            if channel_name in enabled_channels_by_name
        )
