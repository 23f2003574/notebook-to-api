from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
    GovernanceIntegrityDeliveryPolicyService,
)
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
from .deployment_governance_provider_capabilities import (
    GovernanceIntegrityProviderCapabilities,
    validate_delivery_policy_capabilities,
)
from .deployment_governance_provider_authentication import (
    GovernanceIntegrityAuthenticationContext,
    GovernanceIntegrityAuthenticationType,
    GovernanceIntegrityProviderAuthenticationService,
)
from .deployment_governance_provider_health import (
    GovernanceIntegrityProviderHealth,
    GovernanceIntegrityProviderHealthStatus,
)
from .deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfiguration,
    GovernanceIntegrityProviderConfigurationService,
)
from .deployment_governance_provider_lifecycle import (
    GovernanceIntegrityProviderState,
)
from .deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
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
        policy: GovernanceIntegrityDeliveryPolicy | None,
        configuration: GovernanceIntegrityProviderConfiguration,
        authentication: GovernanceIntegrityAuthenticationContext,
    ) -> None:
        """
        Deliver one notification through this provider's channel.

        policy is the channel's configured delivery policy (retry,
        timeout, rate limit), or None if no policy has been
        configured. configuration is this provider's typed runtime
        settings, or an empty configuration if none have been stored.
        authentication is the provider-ready authentication context
        built from this provider's authentication type, resolved
        configuration, and resolved secrets: secrets themselves are
        never passed here. Raises on failure. A stub provider that
        does not perform external I/O simply returns, and may ignore
        policy, configuration, and authentication values entirely.
        """

    def capabilities(self) -> GovernanceIntegrityProviderCapabilities:
        """
        Return this provider's feature-support capabilities, used to
        validate a channel's delivery policy before delivery.
        """

    def health_check(self) -> GovernanceIntegrityProviderHealth:
        """
        Return this provider's current operational health, checked
        before delivery is attempted.
        """

    def authentication_type(self) -> GovernanceIntegrityAuthenticationType:
        """
        Return the authentication scheme this provider expects, used
        to build its authentication context before delivery.
        """


class EmailProvider:
    """
    Local stub email provider: performs no external I/O and always
    succeeds. Ignores the resolved delivery policy, configuration, and authentication.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
        policy: GovernanceIntegrityDeliveryPolicy | None,
        configuration: GovernanceIntegrityProviderConfiguration,
        authentication: GovernanceIntegrityAuthenticationContext,
    ) -> None:
        return

    def capabilities(self) -> GovernanceIntegrityProviderCapabilities:
        return GovernanceIntegrityProviderCapabilities(
            supports_retry=True,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=True,
            supports_markdown=False,
        )

    def health_check(self) -> GovernanceIntegrityProviderHealth:
        return GovernanceIntegrityProviderHealth(
            channel_type=GovernanceIntegrityNotificationChannelType.EMAIL,
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            message=None,
        )

    def authentication_type(self) -> GovernanceIntegrityAuthenticationType:
        return GovernanceIntegrityAuthenticationType.NONE


class SlackProvider:
    """
    Local stub Slack provider: performs no external I/O and always
    succeeds. Ignores the resolved delivery policy, configuration, and authentication.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
        policy: GovernanceIntegrityDeliveryPolicy | None,
        configuration: GovernanceIntegrityProviderConfiguration,
        authentication: GovernanceIntegrityAuthenticationContext,
    ) -> None:
        return

    def capabilities(self) -> GovernanceIntegrityProviderCapabilities:
        return GovernanceIntegrityProviderCapabilities(
            supports_retry=True,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=True,
            supports_markdown=True,
        )

    def health_check(self) -> GovernanceIntegrityProviderHealth:
        return GovernanceIntegrityProviderHealth(
            channel_type=GovernanceIntegrityNotificationChannelType.SLACK,
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            message=None,
        )

    def authentication_type(self) -> GovernanceIntegrityAuthenticationType:
        return GovernanceIntegrityAuthenticationType.BEARER_TOKEN


class WebhookProvider:
    """
    Local stub webhook provider: performs no external I/O and always
    succeeds. Ignores the resolved delivery policy, configuration, and authentication.
    """

    def deliver(
        self,
        dispatch: GovernanceIntegrityNotificationDispatch,
        notification: GovernanceIntegrityNotification,
        channel: GovernanceIntegrityNotificationChannel,
        policy: GovernanceIntegrityDeliveryPolicy | None,
        configuration: GovernanceIntegrityProviderConfiguration,
        authentication: GovernanceIntegrityAuthenticationContext,
    ) -> None:
        return

    def capabilities(self) -> GovernanceIntegrityProviderCapabilities:
        return GovernanceIntegrityProviderCapabilities(
            supports_retry=True,
            supports_timeout=True,
            supports_rate_limit=True,
            supports_attachments=False,
            supports_markdown=False,
        )

    def health_check(self) -> GovernanceIntegrityProviderHealth:
        return GovernanceIntegrityProviderHealth(
            channel_type=(
                GovernanceIntegrityNotificationChannelType.WEBHOOK
            ),
            status=GovernanceIntegrityProviderHealthStatus.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            message=None,
        )

    def authentication_type(self) -> GovernanceIntegrityAuthenticationType:
        return GovernanceIntegrityAuthenticationType.API_KEY


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
        provider_registry: GovernanceIntegrityProviderRegistry,
        policy_service: GovernanceIntegrityDeliveryPolicyService,
        configuration_service: (
            GovernanceIntegrityProviderConfigurationService
        ),
        authentication_service: (
            GovernanceIntegrityProviderAuthenticationService
        ),
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dispatch_repository = dispatch_repository

        self._notification_repository = notification_repository

        self._channel_repository = channel_repository

        self._provider_registry = provider_registry

        self._policy_service = policy_service

        self._configuration_service = configuration_service

        self._authentication_service = authentication_service

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

            metadata = self._provider_registry.metadata(
                channel.channel_type
            )

            if (
                metadata.state
                is GovernanceIntegrityProviderState.DISABLED
            ):
                raise RuntimeError("Provider is disabled.")

            provider = self._provider_registry.resolve(
                channel.channel_type
            )

            health = self._provider_registry.health(
                channel.channel_type
            )

            if (
                health.status
                is GovernanceIntegrityProviderHealthStatus.UNHEALTHY
            ):
                raise RuntimeError(
                    health.message
                    or (
                        "delivery provider for channel type "
                        f"'{channel.channel_type.value}' is unhealthy"
                    )
                )

            try:
                policy = self._policy_service.resolve(channel.name)

            except LookupError:
                policy = None

            if policy is not None:
                capabilities = self._provider_registry.capabilities(
                    channel.channel_type
                )

                validate_delivery_policy_capabilities(
                    policy, capabilities
                )

            configuration = self._configuration_service.resolve(
                channel.channel_type
            )

            authentication = self._authentication_service.build(
                channel.channel_type
            )

            provider.deliver(
                dispatch,
                notification,
                channel,
                policy,
                configuration,
                authentication,
            )

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
