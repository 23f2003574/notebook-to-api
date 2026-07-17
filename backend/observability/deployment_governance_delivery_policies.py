from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from threading import RLock
from typing import Protocol, runtime_checkable

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelService,
)


@dataclass(frozen=True)
class GovernanceIntegrityDeliveryPolicy:
    """
    Per-channel delivery behavior configuration: retry, timeout, and
    rate-limit settings a delivery provider may honor.

    This commit configures delivery behavior only; current stub
    providers may ignore these values.
    """

    channel_name: str

    retry_limit: int

    timeout_seconds: int

    rate_limit_per_minute: int

    enabled: bool

    def __post_init__(self) -> None:
        if not self.channel_name.strip():
            raise ValueError(
                "channel_name must not be empty"
            )

        if self.retry_limit < 0:
            raise ValueError(
                "retry_limit must be greater than or equal to zero"
            )

        if self.timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

        if self.rate_limit_per_minute <= 0:
            raise ValueError(
                "rate_limit_per_minute must be greater than zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "channel_name": self.channel_name,
            "retry_limit": self.retry_limit,
            "timeout_seconds": self.timeout_seconds,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "enabled": self.enabled,
        }


class GovernanceIntegrityDeliveryPolicyError(
    RuntimeError
):
    """
    Base error for governance audit delivery policy persistence
    failures.
    """


class GovernanceIntegrityDeliveryPolicyAlreadyExistsError(
    GovernanceIntegrityDeliveryPolicyError
):
    """
    Raised when a policy for the same channel already exists.
    """


@runtime_checkable
class GovernanceIntegrityDeliveryPolicyRepository(Protocol):
    """
    Persistence contract for per-channel governance audit delivery
    policies.
    """

    def save(
        self,
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> GovernanceIntegrityDeliveryPolicy:
        """
        Persist one policy. Raises if a policy for this channel
        already exists.
        """

    def get(
        self,
        channel_name: str,
    ) -> GovernanceIntegrityDeliveryPolicy | None:
        """
        Return one policy by channel name, or None if it does not
        exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryPolicy,
        ...
    ]:
        """
        Return every policy, ordered by channel name.
        """

    def update(
        self,
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> GovernanceIntegrityDeliveryPolicy:
        """
        Replace an existing policy's stored state. Raises KeyError if
        it does not exist.
        """

    def delete(
        self,
        channel_name: str,
    ) -> None:
        """
        Delete one policy by channel name. Raises KeyError if it does
        not exist.
        """

    def exists(
        self,
        channel_name: str,
    ) -> bool:
        """
        Return whether a policy exists for this channel.
        """


class InMemoryGovernanceIntegrityDeliveryPolicyRepository:
    """
    Thread-safe in-memory implementation of governance audit delivery
    policy storage.
    """

    def __init__(self) -> None:
        self._policies: dict[
            str,
            GovernanceIntegrityDeliveryPolicy,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> GovernanceIntegrityDeliveryPolicy:
        with self._lock:
            if policy.channel_name in self._policies:
                raise (
                    GovernanceIntegrityDeliveryPolicyAlreadyExistsError(
                        "delivery policy for channel "
                        f"'{policy.channel_name}' already exists"
                    )
                )

            self._policies[policy.channel_name] = policy

            return policy

    def get(
        self,
        channel_name: str,
    ) -> GovernanceIntegrityDeliveryPolicy | None:
        normalized_name = self._normalize(channel_name)

        with self._lock:
            return self._policies.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryPolicy,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._policies.values(),
                    key=lambda policy: policy.channel_name,
                )
            )

    def update(
        self,
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> GovernanceIntegrityDeliveryPolicy:
        with self._lock:
            if policy.channel_name not in self._policies:
                raise KeyError(
                    "delivery policy for channel "
                    f"'{policy.channel_name}' was not found"
                )

            self._policies[policy.channel_name] = policy

            return policy

    def delete(
        self,
        channel_name: str,
    ) -> None:
        normalized_name = self._normalize(channel_name)

        with self._lock:
            if normalized_name not in self._policies:
                raise KeyError(
                    f"delivery policy for channel '{normalized_name}' "
                    "was not found"
                )

            del self._policies[normalized_name]

    def exists(
        self,
        channel_name: str,
    ) -> bool:
        normalized_name = self._normalize(channel_name)

        with self._lock:
            return normalized_name in self._policies

    @staticmethod
    def _normalize(channel_name: str) -> str:
        normalized_name = channel_name.strip()

        if not normalized_name:
            raise ValueError(
                "channel_name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityDeliveryPolicyService:
    """
    Creates and manages per-channel governance audit delivery
    policies, and resolves the policy a delivery engine should expose
    to providers for a given channel.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityDeliveryPolicyRepository,
        channel_service: GovernanceIntegrityNotificationChannelService,
    ) -> None:
        self._repository = repository

        self._channel_service = channel_service

    def create(
        self,
        channel_name: str,
        retry_limit: int,
        timeout_seconds: int,
        rate_limit_per_minute: int,
    ) -> GovernanceIntegrityDeliveryPolicy:
        """
        Create a new policy for a channel, enabled by default.

        Raises LookupError if the referenced channel does not exist,
        and ValueError if a policy for this channel already exists.
        """

        if self._channel_service.get(channel_name) is None:
            raise LookupError(
                f"notification channel '{channel_name}' was not found"
            )

        if self._repository.exists(channel_name):
            raise ValueError(
                f"delivery policy for channel '{channel_name}' "
                "already exists"
            )

        policy = GovernanceIntegrityDeliveryPolicy(
            channel_name=channel_name,
            retry_limit=retry_limit,
            timeout_seconds=timeout_seconds,
            rate_limit_per_minute=rate_limit_per_minute,
            enabled=True,
        )

        return self._repository.save(policy)

    def get(
        self,
        channel_name: str,
    ) -> GovernanceIntegrityDeliveryPolicy | None:
        return self._repository.get(channel_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryPolicy,
        ...
    ]:
        return self._repository.list()

    def update(
        self,
        channel_name: str,
        *,
        retry_limit: int | None = None,
        timeout_seconds: int | None = None,
        rate_limit_per_minute: int | None = None,
        enabled: bool | None = None,
    ) -> GovernanceIntegrityDeliveryPolicy:
        """
        Update an existing policy's retry, timeout, rate-limit, and/or
        enabled settings.

        Fields left as None keep their current value. Raises KeyError
        if no policy exists for this channel.
        """

        existing = self._repository.get(channel_name)

        if existing is None:
            raise KeyError(
                f"delivery policy for channel '{channel_name}' was "
                "not found"
            )

        updated = dataclasses.replace(
            existing,
            retry_limit=(
                existing.retry_limit
                if retry_limit is None
                else retry_limit
            ),
            timeout_seconds=(
                existing.timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            rate_limit_per_minute=(
                existing.rate_limit_per_minute
                if rate_limit_per_minute is None
                else rate_limit_per_minute
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
        channel_name: str,
    ) -> None:
        """
        Delete a policy by channel name. Raises KeyError if it does
        not exist.
        """

        self._repository.delete(channel_name)

    def resolve(
        self,
        channel_name: str,
    ) -> GovernanceIntegrityDeliveryPolicy:
        """
        Return the configured policy for one channel.

        Raises LookupError if no policy has been configured for this
        channel.
        """

        policy = self._repository.get(channel_name)

        if policy is None:
            raise LookupError(
                f"delivery policy for channel '{channel_name}' was "
                "not found"
            )

        return policy
