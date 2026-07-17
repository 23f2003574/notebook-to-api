from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertPolicy,
    GovernanceIntegrityAlertSeverity,
    GovernanceIntegrityExecutionAlertService,
)


class GovernanceIntegrityNotificationStatus(
    str,
    Enum,
):
    """
    Lifecycle status of a queued notification.

    Only PENDING exists in this commit: the pipeline converts alerts
    into delivery requests but nothing delivers them yet.
    """

    PENDING = "pending"


@dataclass(frozen=True)
class GovernanceIntegrityNotification:
    """
    One queued delivery request, converted from a generated execution
    alert.
    """

    notification_id: str

    alert_id: str

    severity: GovernanceIntegrityAlertSeverity

    message: str

    status: GovernanceIntegrityNotificationStatus

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.notification_id.strip():
            raise ValueError(
                "notification_id must not be empty"
            )

        if not self.alert_id.strip():
            raise ValueError(
                "alert_id must not be empty"
            )

        if not self.message.strip():
            raise ValueError(
                "message must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "notification_id": self.notification_id,
            "alert_id": self.alert_id,
            "severity": self.severity.value,
            "message": self.message,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }


@runtime_checkable
class GovernanceIntegrityNotificationRepository(Protocol):
    """
    Persistence contract for queued governance audit notifications.
    """

    def save(
        self,
        notification: GovernanceIntegrityNotification,
    ) -> GovernanceIntegrityNotification:
        """
        Persist one notification, replacing any existing notification
        with the same notification_id.
        """

    def get(
        self,
        notification_id: str,
    ) -> GovernanceIntegrityNotification | None:
        """
        Return one notification by id, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotification,
        ...
    ]:
        """
        Return every notification, newest to oldest.
        """

    def delete(
        self,
        notification_id: str,
    ) -> None:
        """
        Remove one notification by id. Raises KeyError if it does not
        exist.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every notification.
        """


class InMemoryGovernanceIntegrityNotificationRepository:
    """
    Thread-safe in-memory implementation of governance audit
    notification storage.
    """

    def __init__(self) -> None:
        self._notifications: dict[
            str,
            GovernanceIntegrityNotification,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        notification: GovernanceIntegrityNotification,
    ) -> GovernanceIntegrityNotification:
        with self._lock:
            self._notifications[
                notification.notification_id
            ] = notification

            return notification

    def get(
        self,
        notification_id: str,
    ) -> GovernanceIntegrityNotification | None:
        normalized_id = self._normalize(notification_id)

        with self._lock:
            return self._notifications.get(normalized_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotification,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._notifications.values(),
                    key=lambda notification: (
                        notification.created_at,
                        notification.notification_id,
                    ),
                    reverse=True,
                )
            )

    def delete(
        self,
        notification_id: str,
    ) -> None:
        normalized_id = self._normalize(notification_id)

        with self._lock:
            if normalized_id not in self._notifications:
                raise KeyError(
                    f"notification '{normalized_id}' was not found"
                )

            del self._notifications[normalized_id]

    def clear(
        self,
    ) -> None:
        with self._lock:
            self._notifications.clear()

    @staticmethod
    def _normalize(notification_id: str) -> str:
        normalized_id = notification_id.strip()

        if not normalized_id:
            raise ValueError(
                "notification_id must not be empty"
            )

        return normalized_id


class GovernanceIntegrityNotificationService:
    """
    Converts generated governance audit execution alerts into queued
    delivery requests.

    No delivery happens in this commit: notifications are queued as
    PENDING, ready for a future delivery provider to consume.
    """

    def __init__(
        self,
        alert_service: GovernanceIntegrityExecutionAlertService,
        repository: GovernanceIntegrityNotificationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._alert_service = alert_service

        self._repository = repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._uuid_factory = uuid_factory or (
            lambda: str(uuid.uuid4())
        )

    def queue(
        self,
        policy: GovernanceIntegrityAlertPolicy,
    ) -> tuple[
        GovernanceIntegrityNotification,
        ...
    ]:
        """
        Generate alerts from the policy and queue one notification per
        newly seen alert.

        An alert whose alert_id already has a queued notification is
        skipped, so re-running queue() with a policy that keeps
        producing the same alert does not create duplicates. Returns
        only the notifications created by this call.
        """

        alerts = self._alert_service.generate(policy)

        already_notified_alert_ids = {
            notification.alert_id
            for notification in self._repository.list()
        }

        queued: list[GovernanceIntegrityNotification] = []

        for alert in alerts:
            if alert.alert_id in already_notified_alert_ids:
                continue

            notification = GovernanceIntegrityNotification(
                notification_id=self._uuid_factory(),
                alert_id=alert.alert_id,
                severity=alert.severity,
                message=alert.message,
                status=GovernanceIntegrityNotificationStatus.PENDING,
                created_at=self._clock(),
            )

            self._repository.save(notification)

            queued.append(notification)

            already_notified_alert_ids.add(alert.alert_id)

        return tuple(queued)

    def get(
        self,
        notification_id: str,
    ) -> GovernanceIntegrityNotification | None:
        return self._repository.get(notification_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotification,
        ...
    ]:
        return self._repository.list()

    def delete(
        self,
        notification_id: str,
    ) -> None:
        """
        Remove one notification. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(notification_id)

    def clear(
        self,
    ) -> None:
        """
        Remove every notification.
        """

        self._repository.clear()
