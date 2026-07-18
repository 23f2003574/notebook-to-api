from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
)
from backend.observability.deployment_governance_provider_responses import (
    GovernanceIntegrityProviderResponseOutcome,
)
from backend.observability.deployment_governance_retry_orchestrator import (
    GovernanceIntegrityRetryDecision,
    GovernanceIntegrityRetryOrchestrator,
    GovernanceIntegrityRetryStrategy,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def _policy(retry_limit: int = 3) -> GovernanceIntegrityDeliveryPolicy:
    return GovernanceIntegrityDeliveryPolicy(
        channel_name="email",
        retry_limit=retry_limit,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        enabled=True,
    )


def _success_outcome() -> GovernanceIntegrityProviderResponseOutcome:
    return GovernanceIntegrityProviderResponseOutcome(
        success=True,
        provider_status="success",
        retryable=False,
        message=None,
        completed_at=BASE_TIME,
    )


def _failure_outcome(retryable: bool) -> GovernanceIntegrityProviderResponseOutcome:
    return GovernanceIntegrityProviderResponseOutcome(
        success=False,
        provider_status="server_error" if retryable else "client_error",
        retryable=retryable,
        message="boom",
        completed_at=BASE_TIME,
    )


# --- Model ---------------------------------------------------------------


def test_decision_requires_next_retry_at_when_retrying() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityRetryDecision(
            should_retry=True,
            retry_attempt=1,
            next_retry_at=None,
            delay_seconds=30,
            reason=None,
        )


def test_decision_rejects_next_retry_at_when_not_retrying() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityRetryDecision(
            should_retry=False,
            retry_attempt=0,
            next_retry_at=BASE_TIME,
            delay_seconds=None,
            reason=None,
        )


def test_decision_to_dict() -> None:
    decision = GovernanceIntegrityRetryDecision(
        should_retry=False,
        retry_attempt=0,
        next_retry_at=None,
        delay_seconds=None,
        reason="not retryable",
    )

    assert decision.to_dict() == {
        "should_retry": False,
        "retry_attempt": 0,
        "next_retry_at": None,
        "delay_seconds": None,
        "reason": "not retryable",
    }


# --- Evaluate: success --------------------------------------------------


def test_successful_delivery_does_not_retry() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(clock=lambda: BASE_TIME)

    decision = orchestrator.evaluate(_success_outcome(), _policy(), 0)

    assert decision.should_retry is False


# --- Evaluate: client error ------------------------------------------


def test_non_retryable_failure_does_not_retry() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(clock=lambda: BASE_TIME)

    decision = orchestrator.evaluate(
        _failure_outcome(retryable=False), _policy(), 0
    )

    assert decision.should_retry is False


# --- Evaluate: server error ------------------------------------------


def test_retryable_failure_schedules_retry() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(clock=lambda: BASE_TIME)

    decision = orchestrator.evaluate(
        _failure_outcome(retryable=True), _policy(retry_limit=3), 0
    )

    assert decision.should_retry is True
    assert decision.next_retry_at is not None
    assert decision.delay_seconds is not None


# --- Evaluate: max retries -----------------------------------------------


def test_max_retries_reached_does_not_retry() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(clock=lambda: BASE_TIME)

    decision = orchestrator.evaluate(
        _failure_outcome(retryable=True), _policy(retry_limit=3), 3
    )

    assert decision.should_retry is False


# --- Exponential backoff --------------------------------------------------


def test_exponential_backoff_delays() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(
        clock=lambda: BASE_TIME,
        strategy=GovernanceIntegrityRetryStrategy.EXPONENTIAL,
        base_delay_seconds=30,
    )

    policy = _policy(retry_limit=10)

    delays = [
        orchestrator.evaluate(
            _failure_outcome(retryable=True), policy, attempt
        ).delay_seconds
        for attempt in range(4)
    ]

    assert delays == [30, 60, 120, 240]


def test_exponential_backoff_respects_max_delay() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(
        clock=lambda: BASE_TIME,
        strategy=GovernanceIntegrityRetryStrategy.EXPONENTIAL,
        base_delay_seconds=30,
        max_delay_seconds=100,
    )

    decision = orchestrator.evaluate(
        _failure_outcome(retryable=True), _policy(retry_limit=10), 3
    )

    assert decision.delay_seconds == 100


def test_fixed_strategy_uses_constant_delay() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(
        clock=lambda: BASE_TIME,
        strategy=GovernanceIntegrityRetryStrategy.FIXED,
        base_delay_seconds=15,
    )

    policy = _policy(retry_limit=10)

    delays = [
        orchestrator.evaluate(
            _failure_outcome(retryable=True), policy, attempt
        ).delay_seconds
        for attempt in range(3)
    ]

    assert delays == [15, 15, 15]


def test_none_strategy_uses_zero_delay() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(
        clock=lambda: BASE_TIME,
        strategy=GovernanceIntegrityRetryStrategy.NONE,
    )

    decision = orchestrator.evaluate(
        _failure_outcome(retryable=True), _policy(retry_limit=10), 0
    )

    assert decision.delay_seconds == 0
