from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, Protocol, runtime_checkable

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
    GovernanceIntegrityNotificationChannelRepository,
    GovernanceIntegrityNotificationChannelType,
)
from .deployment_governance_notification_dispatcher import (
    GovernanceIntegrityNotificationDispatch,
    GovernanceIntegrityNotificationDispatchRepository,
)
from .deployment_governance_notifications import (
    GovernanceIntegrityNotification,
    GovernanceIntegrityNotificationRepository,
)


class GovernanceIntegrityDeliveryStatus(
    str,
    Enum,
):
    """
    Outcome of one attempt to deliver a queued dispatch through its
    resolved provider.
    """

    SUCCESS = "success"

    FAILED = "failed"


@dataclass(frozen=True)
class GovernanceIntegrityDeliveryResult:
    """
    The outcome of delivering one queued dispatch: either the
    delivery succeeded, or the reason it did not.
    """

    dispatch_id: str

    channel_name: str

    status: GovernanceIntegrityDeliveryStatus

    delivered_at: datetime

    error: str | None

    def __post_init__(self) -> None:
        if not self.dispatch_id.strip():
            raise ValueError(
                "dispatch_id must not be empty"
            )

        if not self.channel_name.strip():
            raise ValueError(
                "channel_name must not be empty"
            )

        if self.delivered_at.tzinfo is None:
            raise ValueError(
                "delivered_at must be timezone-aware"
            )

        if self.status is GovernanceIntegrityDeliveryStatus.SUCCESS:
            if self.error is not None:
                raise ValueError(
                    "error must not be set when status is SUCCESS"
                )

        else:
            if self.error is None:
                raise ValueError(
                    "error must be set when status is FAILED"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "dispatch_id": self.dispatch_id,
            "channel_name": self.channel_name,
            "status": self.status.value,
            "delivered_at": self.delivered_at.isoformat(),
            "error": self.error,
        }


@runtime_checkable
class GovernanceIntegrityNotificationProvider(Protocol):
    """
    A pluggable delivery mechanism for one notification channel type.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> None:
        """
        Deliver one notification through this provider's channel.

        Raises on failure. A stub provider that does not perform
        external I/O simply returns.
        """


class EmailProvider:
    """
    Local stub email provider: performs no external I/O and always
    succeeds.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> None:
        return


class SlackProvider:
    """
    Local stub Slack provider: performs no external I/O and always
    succeeds.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> None:
        return


class WebhookProvider:
    """
    Local stub webhook provider: performs no external I/O and always
    succeeds.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> None:
        return


class GovernanceIntegrityDeliveryEngine:
    """
    Executes queued governance audit notification dispatches through
    pluggable, per-channel-type providers.

    Providers are local stubs in this commit: delivery never performs
    external I/O.
    """

    def __init__(
        self,
        dispatch_repository: (
            GovernanceIntegrityNotificationDispatchRepository
        ),
        notification_repository: (
            GovernanceIntegrityNotificationRepository
        ),
        channel_repository: (
            GovernanceIntegrityNotificationChannelRepository
        ),
        provider_registry: Mapping[
            GovernanceIntegrityNotificationChannelType,
            GovernanceIntegrityNotificationProvider,
        ],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dispatch_repository = dispatch_repository

        self._notification_repository = notification_repository

        self._channel_repository = channel_repository

        self._provider_registry = provider_registry

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def deliver(
        self,
        dispatch_id: str,
    ) -> GovernanceIntegrityDeliveryResult:
        """
        Load one queued dispatch, resolve its notification, channel,
        and provider, and attempt delivery.

        Raises KeyError if the dispatch does not exist. Missing
        notifications, missing channels, missing providers, and
        provider delivery failures are all captured as a FAILED
        result rather than raised.
        """

        dispatch = self._dispatch_repository.get(dispatch_id)

        if dispatch is None:
            raise KeyError(
                f"notification dispatch '{dispatch_id}' was not found"
            )

        try:
            notification = self._notification_repository.get(
                dispatch.notification_id
            )

            if notification is None:
                raise LookupError(
                    f"notification '{dispatch.notification_id}' "
                    "was not found"
                )

            channel = self._channel_repository.get(
                dispatch.channel_name
            )

            if channel is None:
                raise LookupError(
                    f"notification channel '{dispatch.channel_name}' "
                    "was not found"
                )

            provider = self._provider_registry.get(
                channel.channel_type
            )

            if provider is None:
                raise LookupError(
                    "no delivery provider registered for channel "
                    f"type '{channel.channel_type.value}'"
                )

            provider.deliver(dispatch, notification, channel)

        except Exception as exc:
            return GovernanceIntegrityDeliveryResult(
                dispatch_id=dispatch.dispatch_id,
                channel_name=dispatch.channel_name,
                status=GovernanceIntegrityDeliveryStatus.FAILED,
                delivered_at=self._clock(),
                error=str(exc),
            )

        return GovernanceIntegrityDeliveryResult(
            dispatch_id=dispatch.dispatch_id,
            channel_name=dispatch.channel_name,
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=self._clock(),
            error=None,
        )

    def deliver_all(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryResult,
        ...
    ]:
        """
        Deliver every currently queued dispatch, sequentially, oldest
        first.
        """

        dispatches = sorted(
            self._dispatch_repository.list(),
            key=lambda dispatch: (
                dispatch.created_at,
                dispatch.dispatch_id,
            ),
        )

        return tuple(
            self.deliver(dispatch.dispatch_id)
            for dispatch in dispatches
        )
