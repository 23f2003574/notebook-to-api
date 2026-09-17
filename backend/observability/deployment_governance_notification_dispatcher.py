from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_notification_preferences import (
    GovernanceIntegrityNotificationPreferenceService,
)
from .deployment_governance_notifications import (
    GovernanceIntegrityNotificationRepository,
    GovernanceIntegrityNotificationStatus,
)


class GovernanceIntegrityDispatchStatus(
    str,
    Enum,
):
    """
    Lifecycle status of a notification dispatch record.

    Only QUEUED exists in this commit: the dispatcher matches
    notifications to channels but no external delivery happens yet.
    """

    QUEUED = "queued"


@dataclass(frozen=True)
class GovernanceIntegrityNotificationDispatch:
    """
    One recorded delivery attempt: a pending notification matched to
    one enabled channel.
    """

    dispatch_id: str

    notification_id: str

    channel_name: str

    status: GovernanceIntegrityDispatchStatus

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.dispatch_id.strip():
            raise ValueError(
                "dispatch_id must not be empty"
            )

        if not self.notification_id.strip():
            raise ValueError(
                "notification_id must not be empty"
            )

        if not self.channel_name.strip():
            raise ValueError(
                "channel_name must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "dispatch_id": self.dispatch_id,
            "notification_id": self.notification_id,
            "channel_name": self.channel_name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }


@runtime_checkable
class GovernanceIntegrityNotificationDispatchRepository(Protocol):
    """
    Persistence contract for governance audit notification dispatch
    records.
    """

    def save(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
    ) -> GovernanceIntegrityNotificationDispatch:
        """
        Persist one dispatch record, replacing any existing record
        with the same dispatch_id.
        """

    def get(
        self,
        dispatch_id: str,
    ) -> GovernanceIntegrityNotificationDispatch | None:
        """
        Return one dispatch record by id, or None if it does not
        exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationDispatch,
        ...
    ]:
        """
        Return every dispatch record, newest to oldest.
        """

    def delete(
        self,
        dispatch_id: str,
    ) -> None:
        """
        Remove one dispatch record by id. Raises KeyError if it does
        not exist.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every dispatch record.
        """


class InMemoryGovernanceIntegrityNotificationDispatchRepository:
    """
    Thread-safe in-memory implementation of governance audit
    notification dispatch storage.
    """

    def __init__(self) -> None:
        self._dispatches: dict[
            str,
            GovernanceIntegrityNotificationDispatch,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
    ) -> GovernanceIntegrityNotificationDispatch:
        with self._lock:
            self._dispatches[dispatch.dispatch_id] = dispatch

            return dispatch

    def get(
        self,
        dispatch_id: str,
    ) -> GovernanceIntegrityNotificationDispatch | None:
        normalized_id = self._normalize(dispatch_id)

        with self._lock:
            return self._dispatches.get(normalized_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationDispatch,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._dispatches.values(),
                    key=lambda dispatch: (
                        dispatch.created_at,
                        dispatch.dispatch_id,
                    ),
                    reverse=True,
                )
            )

    def delete(
        self,
        dispatch_id: str,
    ) -> None:
        normalized_id = self._normalize(dispatch_id)

        with self._lock:
            if normalized_id not in self._dispatches:
                raise KeyError(
                    f"notification dispatch '{normalized_id}' "
                    "was not found"
                )

            del self._dispatches[normalized_id]

    def clear(
        self,
    ) -> None:
        with self._lock:
            self._dispatches.clear()

    @staticmethod
    def _normalize(dispatch_id: str) -> str:
        normalized_id = dispatch_id.strip()

        if not normalized_id:
            raise ValueError(
                "dispatch_id must not be empty"
            )

        return normalized_id


class GovernanceIntegrityNotificationDispatcher:
    """
    Matches pending governance audit notifications to the channels
    their severity is routed to, and records the resulting dispatch
    attempts.

    No external delivery happens in this commit: dispatch_pending()
    only records that a notification was matched to a channel.
    """

    def __init__(
        self,
        notification_repository: (
            GovernanceIntegrityNotificationRepository
        ),
        preference_service: (
            GovernanceIntegrityNotificationPreferenceService
        ),
        dispatch_repository: (
            GovernanceIntegrityNotificationDispatchRepository
        ),
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._notification_repository = notification_repository

        self._preference_service = preference_service

        self._dispatch_repository = dispatch_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._uuid_factory = uuid_factory or (
            lambda: str(uuid.uuid4())
        )

    def dispatch_pending(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationDispatch,
        ...
    ]:
        """
        Match every pending notification to the channels its severity
        resolves to (per enabled routing preferences) and persist one
        dispatch record per new (notification, channel) pair.

        A pair that already has a dispatch record is skipped, so
        re-running this does not create duplicates. Returns only the
        dispatch records created by this call.
        """

        pending_notifications = tuple(
            notification
            for notification in self._notification_repository.list()
            if notification.status
            is GovernanceIntegrityNotificationStatus.PENDING
        )

        existing_pairs = {
            (dispatch.notification_id, dispatch.channel_name)
            for dispatch in self._dispatch_repository.list()
        }

        created: list[GovernanceIntegrityNotificationDispatch] = []

        for notification in pending_notifications:
            matching_channels = self._preference_service.resolve(
                notification.severity
            )

            for channel in matching_channels:
                pair = (notification.notification_id, channel.name)

                if pair in existing_pairs:
                    continue

                dispatch = GovernanceIntegrityNotificationDispatch(
                    dispatch_id=self._uuid_factory(),
                    notification_id=notification.notification_id,
                    channel_name=channel.name,
                    status=GovernanceIntegrityDispatchStatus.QUEUED,
                    created_at=self._clock(),
                )

                self._dispatch_repository.save(dispatch)

                created.append(dispatch)

                existing_pairs.add(pair)

        return tuple(created)

    def get(
        self,
        dispatch_id: str,
    ) -> GovernanceIntegrityNotificationDispatch | None:
        return self._dispatch_repository.get(dispatch_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationDispatch,
        ...
    ]:
        return self._dispatch_repository.list()

    def delete(
        self,
        dispatch_id: str,
    ) -> None:
        """
        Remove one dispatch record. Raises KeyError if it does not
        exist.
        """

        self._dispatch_repository.delete(dispatch_id)

    def clear(
        self,
    ) -> None:
        """
        Remove every dispatch record.
        """

        self._dispatch_repository.clear()
