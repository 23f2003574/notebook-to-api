from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable

from .deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
)
from .deployment_governance_provider_responses import (
    GovernanceIntegrityProviderResponseOutcome,
)

DEFAULT_BASE_DELAY_SECONDS = 30


class GovernanceIntegrityRetryStrategy(
    str,
    Enum,
):
    """
    How the delay before a retry attempt grows across attempts.
    """

    NONE = "none"

    FIXED = "fixed"

    EXPONENTIAL = "exponential"


@dataclass(frozen=True)
class GovernanceIntegrityRetryDecision:
    """
    Whether a failed delivery should be retried, and if so, when.
    """

    should_retry: bool

    retry_attempt: int

    next_retry_at: datetime | None

    delay_seconds: int | None

    reason: str | None

    def __post_init__(self) -> None:
        if self.retry_attempt < 0:
            raise ValueError(
                "retry_attempt must be greater than or equal to zero"
            )

        if self.should_retry:
            if self.next_retry_at is None or self.delay_seconds is None:
                raise ValueError(
                    "next_retry_at and delay_seconds must be set "
                    "when should_retry is True"
                )

            if self.next_retry_at.tzinfo is None:
                raise ValueError(
                    "next_retry_at must be timezone-aware"
                )

            if self.delay_seconds < 0:
                raise ValueError(
                    "delay_seconds must be greater than or equal to "
                    "zero"
                )

        else:
            if self.next_retry_at is not None or self.delay_seconds is not None:
                raise ValueError(
                    "next_retry_at and delay_seconds must not be set "
                    "when should_retry is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "should_retry": self.should_retry,
            "retry_attempt": self.retry_attempt,
            "next_retry_at": (
                None
                if self.next_retry_at is None
                else self.next_retry_at.isoformat()
            ),
            "delay_seconds": self.delay_seconds,
            "reason": self.reason,
        }


class GovernanceIntegrityRetryOrchestrator:
    """
    Decides whether a failed delivery should be retried, and computes
    the delay before the next attempt, independent of the delivery
    engine itself.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        strategy: GovernanceIntegrityRetryStrategy = (
            GovernanceIntegrityRetryStrategy.EXPONENTIAL
        ),
        base_delay_seconds: int = DEFAULT_BASE_DELAY_SECONDS,
        max_delay_seconds: int | None = None,
    ) -> None:
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._strategy = strategy

        self._base_delay_seconds = base_delay_seconds

        self._max_delay_seconds = max_delay_seconds

    def evaluate(
        self,
        delivery_result: GovernanceIntegrityProviderResponseOutcome,
        policy: GovernanceIntegrityDeliveryPolicy,
        attempt: int,
    ) -> GovernanceIntegrityRetryDecision:
        """
        Decide whether one failed delivery attempt should be retried.

        delivery_result is the normalized outcome of the delivery
        attempt just made. policy provides the channel's configured
        retry_limit. attempt is the zero-based number of attempts
        already made (0 for the first attempt).

        A successful delivery, a non-retryable failure, or having
        already reached the policy's retry_limit all mean no retry.
        Otherwise a retry is scheduled using this orchestrator's
        configured strategy.
        """

        if delivery_result.success:
            return GovernanceIntegrityRetryDecision(
                should_retry=False,
                retry_attempt=attempt,
                next_retry_at=None,
                delay_seconds=None,
                reason=None,
            )

        if not delivery_result.retryable:
            return GovernanceIntegrityRetryDecision(
                should_retry=False,
                retry_attempt=attempt,
                next_retry_at=None,
                delay_seconds=None,
                reason="delivery failure is not retryable",
            )

        if attempt >= policy.retry_limit:
            return GovernanceIntegrityRetryDecision(
                should_retry=False,
                retry_attempt=attempt,
                next_retry_at=None,
                delay_seconds=None,
                reason="maximum retry attempts reached",
            )

        delay_seconds = self._compute_delay_seconds(attempt)

        return GovernanceIntegrityRetryDecision(
            should_retry=True,
            retry_attempt=attempt + 1,
            next_retry_at=(
                self._clock() + timedelta(seconds=delay_seconds)
            ),
            delay_seconds=delay_seconds,
            reason=None,
        )

    def _compute_delay_seconds(self, attempt: int) -> int:
        if self._strategy is GovernanceIntegrityRetryStrategy.NONE:
            delay_seconds = 0

        elif self._strategy is GovernanceIntegrityRetryStrategy.FIXED:
            delay_seconds = self._base_delay_seconds

        else:
            delay_seconds = self._base_delay_seconds * (2 ** attempt)

        if self._max_delay_seconds is not None:
            delay_seconds = min(delay_seconds, self._max_delay_seconds)

        return delay_seconds
