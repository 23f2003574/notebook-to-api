from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional

from fastapi import APIRouter, Body, HTTPException, Query

NOTIFICATION_STATUSES = ("SUCCESS", "FAILED")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownChannelError(KeyError):
    pass


class UnknownNotificationError(KeyError):
    pass


class NotificationChannel:
    """Common interface every notification channel must implement."""

    def __init__(self, name: str, *, enabled: bool = True) -> None:
        self.name = name
        self.enabled = enabled

    def deliver(self, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError


class InMemoryChannel(NotificationChannel):
    """
    A channel that simulates delivery through a pluggable transport,
    defaulting to an in-memory sink so the channel is safe to use
    without real network access.
    """

    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        transport: Optional[Callable[[Mapping[str, Any]], None]] = None,
    ) -> None:
        super().__init__(name, enabled=enabled)
        self._transport = transport
        self._delivered: list[dict] = []

    def deliver(self, payload: Mapping[str, Any]) -> None:
        if self._transport is not None:
            self._transport(payload)
        self._delivered.append(dict(payload))

    @property
    def delivered(self) -> list[dict]:
        return list(self._delivered)


class EmailChannel(InMemoryChannel):
    def __init__(self, *, enabled: bool = True, transport=None) -> None:
        super().__init__("email", enabled=enabled, transport=transport)


class WebhookChannel(InMemoryChannel):
    def __init__(self, *, enabled: bool = True, transport=None) -> None:
        super().__init__("webhook", enabled=enabled, transport=transport)


class SlackChannel(InMemoryChannel):
    def __init__(self, *, enabled: bool = True, transport=None) -> None:
        super().__init__("slack", enabled=enabled, transport=transport)


class ConsoleChannel(InMemoryChannel):
    def __init__(self, *, enabled: bool = True, transport=None) -> None:
        super().__init__("console", enabled=enabled, transport=transport)


@dataclass(frozen=True)
class NotificationRecord:
    """One immutable record of a notification delivery attempt."""

    notification_id: str
    channel: str
    alert_id: str
    status: str
    attempts: int
    created_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "channel": self.channel,
            "alert_id": self.alert_id,
            "status": self.status,
            "attempts": self.attempts,
            "created_at": self.created_at.isoformat(),
            "payload": dict(self.payload),
            "error": self.error,
        }


class DeploymentNotificationService:
    """Routes and delivers alerts to registered notification channels."""

    def __init__(self, channels: Optional[Iterable[NotificationChannel]] = None) -> None:
        self._channels: dict[str, NotificationChannel] = {}
        self._history: list[NotificationRecord] = []
        self._history_by_id: dict[str, NotificationRecord] = {}
        self._lock = Lock()
        for channel in (
            (EmailChannel(), WebhookChannel(), SlackChannel(), ConsoleChannel())
            if channels is None
            else channels
        ):
            self.register_channel(channel)

    def register_channel(self, channel: NotificationChannel) -> None:
        with self._lock:
            self._channels[channel.name] = channel

    def get_channel(self, name: str) -> NotificationChannel:
        with self._lock:
            channel = self._channels.get(name)
        if channel is None:
            raise UnknownChannelError(name)
        return channel

    def list_channels(self) -> list[dict]:
        with self._lock:
            channels = list(self._channels.values())
        return [{"name": c.name, "enabled": c.enabled} for c in channels]

    def notify(
        self,
        alert: Any,
        *,
        channels: Optional[Iterable[str]] = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.05,
        sleep: Callable[[float], None] = time.sleep,
        timestamp: Optional[datetime] = None,
    ) -> list[NotificationRecord]:
        alert_id = (
            alert.get("alert_id")
            if isinstance(alert, Mapping)
            else getattr(alert, "alert_id", None)
        )
        if not alert_id:
            raise ValueError("alert must provide an 'alert_id'")
        payload = alert.to_dict() if hasattr(alert, "to_dict") else dict(alert)

        with self._lock:
            target_names = (
                list(channels)
                if channels is not None
                else [
                    name
                    for name, channel in self._channels.items()
                    if channel.enabled
                ]
            )

        records = []
        for name in target_names:
            channel = self.get_channel(name)
            if not channel.enabled:
                continue
            record = self._deliver_with_retry(
                channel,
                alert_id,
                payload,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
                timestamp=timestamp,
            )
            records.append(record)
        return records

    def retry(
        self,
        notification_id: str,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.05,
        sleep: Callable[[float], None] = time.sleep,
        timestamp: Optional[datetime] = None,
    ) -> NotificationRecord:
        with self._lock:
            record = self._history_by_id.get(notification_id)
        if record is None:
            raise UnknownNotificationError(notification_id)
        channel = self.get_channel(record.channel)
        return self._deliver_with_retry(
            channel,
            record.alert_id,
            record.payload,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
            timestamp=timestamp,
        )

    def history(self, channel: Optional[str] = None) -> list[NotificationRecord]:
        with self._lock:
            records = list(self._history)
        if channel is not None:
            records = [r for r in records if r.channel == channel]
        return records

    def _deliver_with_retry(
        self,
        channel: NotificationChannel,
        alert_id: str,
        payload: Mapping[str, Any],
        *,
        max_attempts: int,
        backoff_seconds: float,
        sleep: Callable[[float], None],
        timestamp: Optional[datetime],
    ) -> NotificationRecord:
        last_error: Optional[str] = None
        for attempt in range(1, max_attempts + 1):
            try:
                channel.deliver(payload)
            except Exception as exc:  # noqa: BLE001 - channel failures are data
                last_error = str(exc)
                if attempt < max_attempts:
                    sleep(backoff_seconds * (2 ** (attempt - 1)))
                continue
            return self._store(
                channel=channel.name,
                alert_id=alert_id,
                status="SUCCESS",
                attempts=attempt,
                payload=payload,
                timestamp=timestamp,
            )
        return self._store(
            channel=channel.name,
            alert_id=alert_id,
            status="FAILED",
            attempts=max_attempts,
            payload=payload,
            timestamp=timestamp,
            error=last_error,
        )

    def _store(
        self,
        *,
        channel: str,
        alert_id: str,
        status: str,
        attempts: int,
        payload: Mapping[str, Any],
        timestamp: Optional[datetime],
        error: Optional[str] = None,
    ) -> NotificationRecord:
        record = NotificationRecord(
            notification_id=_new_id(),
            channel=channel,
            alert_id=alert_id,
            status=status,
            attempts=attempts,
            created_at=timestamp or datetime.now(timezone.utc),
            payload=payload,
            error=error,
        )
        with self._lock:
            self._history.append(record)
            self._history_by_id[record.notification_id] = record
        return record


_service = DeploymentNotificationService()


def get_deployment_notification_service() -> DeploymentNotificationService:
    return _service


router = APIRouter(prefix="/governance", tags=["governance-notifications"])


@router.post("/notifications/send")
def send_notification(payload: dict = Body(...)) -> list[dict]:
    alert_id = payload.get("alert_id")
    if not alert_id:
        raise HTTPException(status_code=422, detail="alert_id is required")
    channels = payload.get("channels")
    try:
        records = get_deployment_notification_service().notify(
            payload, channels=channels
        )
    except UnknownChannelError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown channel: {exc.args[0]}"
        )
    return [record.to_dict() for record in records]


@router.get("/notifications/history")
def notification_history(
    channel: Optional[str] = Query(default=None),
) -> list[dict]:
    records = get_deployment_notification_service().history(channel=channel)
    return [record.to_dict() for record in records]


@router.get("/notifications/channels")
def list_channels() -> list[dict]:
    return get_deployment_notification_service().list_channels()
