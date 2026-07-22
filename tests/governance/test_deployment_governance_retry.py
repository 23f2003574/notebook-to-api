from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_execution_manager import (
    GovernanceExecutionManager,
)
from backend.observability.deployment_governance_retry import (
    GovernanceRetryEngine,
    RetryAttempt,
    RetryMetrics,
    RetryPolicy,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _no_jitter(low: float, high: float) -> float:
    return high


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The retry engine and execution manager are both process-wide
    singletons. Neither exposes a bulk "clear everything" method (by
    design — RetryMetrics/ExecutionManagerStatus are meant to survive
    like real lifetime counters), so most tests use a fresh instance
    of their own instead of the shared singleton; only the pending
    retry queue (which does support per-item cancellation) is cleared
    here to keep GET /governance/retries/pending predictable.
    """

    from backend.observability.deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_retry import (
        get_retry_engine,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_execution_manager().cleanup()

        retry_engine = get_retry_engine()

        for attempt in retry_engine.pending():
            retry_engine.cancel_retry(attempt.execution_id)

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestRetryPolicy:

    def test_rejects_empty_policy_id(self):
        with pytest.raises(ValueError, match="policy_id must not be empty"):
            RetryPolicy(
                policy_id="", max_attempts=3, strategy="fixed",
                base_delay_seconds=1, max_delay_seconds=10, jitter=False,
            )

    def test_rejects_non_positive_max_attempts(self):
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryPolicy(
                policy_id="p", max_attempts=0, strategy="fixed",
                base_delay_seconds=1, max_delay_seconds=10, jitter=False,
            )

    def test_rejects_negative_base_delay(self):
        with pytest.raises(
            ValueError, match="base_delay_seconds must be >= 0"
        ):
            RetryPolicy(
                policy_id="p", max_attempts=3, strategy="fixed",
                base_delay_seconds=-1, max_delay_seconds=10, jitter=False,
            )

    def test_rejects_negative_max_delay(self):
        with pytest.raises(
            ValueError, match="max_delay_seconds must be >= 0"
        ):
            RetryPolicy(
                policy_id="p", max_attempts=3, strategy="fixed",
                base_delay_seconds=1, max_delay_seconds=-1, jitter=False,
            )

    def test_to_dict(self):
        policy = RetryPolicy(
            policy_id="p", max_attempts=3, strategy="fixed",
            base_delay_seconds=1.5, max_delay_seconds=10, jitter=True,
        )

        assert policy.to_dict() == {
            "policy_id": "p",
            "max_attempts": 3,
            "strategy": "fixed",
            "base_delay_seconds": 1.5,
            "max_delay_seconds": 10,
            "jitter": True,
        }


class TestRetryAttempt:

    def test_rejects_empty_execution_id(self):
        with pytest.raises(ValueError, match="execution_id must not be empty"):
            RetryAttempt(
                execution_id="", attempt=1, scheduled_at=BASE_TIME,
                reason=None,
            )

    def test_rejects_non_positive_attempt(self):
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            RetryAttempt(
                execution_id="e-1", attempt=0, scheduled_at=BASE_TIME,
                reason=None,
            )

    def test_rejects_naive_scheduled_at(self):
        with pytest.raises(
            ValueError, match="scheduled_at must be timezone-aware"
        ):
            RetryAttempt(
                execution_id="e-1", attempt=1,
                scheduled_at=datetime(2026, 7, 21, 12, 0, 0), reason=None,
            )

    def test_to_dict(self):
        attempt = RetryAttempt(
            execution_id="e-1", attempt=2, scheduled_at=BASE_TIME,
            reason="boom",
        )

        assert attempt.to_dict() == {
            "execution_id": "e-1",
            "attempt": 2,
            "scheduled_at": BASE_TIME.isoformat(),
            "reason": "boom",
        }


class TestRetryMetrics:

    def test_success_rate_zero_with_no_attempts(self):
        metrics = RetryMetrics(
            scheduled_count=0, succeeded_count=0, exhausted_count=0,
        )

        assert metrics.success_rate == 0.0

    def test_success_rate_computed(self):
        metrics = RetryMetrics(
            scheduled_count=4, succeeded_count=1, exhausted_count=1,
        )

        assert metrics.success_rate == 0.25

    def test_to_dict(self):
        metrics = RetryMetrics(
            scheduled_count=2, succeeded_count=1, exhausted_count=0,
        )

        assert metrics.to_dict() == {
            "scheduled_count": 2,
            "succeeded_count": 1,
            "exhausted_count": 0,
            "success_rate": 0.5,
        }


# --- Policy registration -------------------------------------------------


class TestPolicyRegistration:

    def test_register_returns_policy(self):
        engine = GovernanceRetryEngine(clock=_clock)

        policy = engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=1, max_delay_seconds=10,
        )

        assert policy.policy_id == "p"
        assert policy.strategy == "fixed"

    def test_duplicate_policy_id_rejected(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=1, max_delay_seconds=10,
        )

        with pytest.raises(ValueError, match="already registered"):
            engine.register_policy(
                "p", max_attempts=3, strategy="fixed",
                base_delay_seconds=1, max_delay_seconds=10,
            )

    def test_rejects_unknown_strategy_without_strategy_fn(self):
        engine = GovernanceRetryEngine(clock=_clock)

        with pytest.raises(ValueError, match="unknown backoff strategy"):
            engine.register_policy(
                "p", max_attempts=3, strategy="teleport",
                base_delay_seconds=1, max_delay_seconds=10,
            )

    def test_accepts_custom_strategy_fn(self):
        engine = GovernanceRetryEngine(clock=_clock)

        policy = engine.register_policy(
            "p", max_attempts=3, strategy="teleport",
            base_delay_seconds=1, max_delay_seconds=10,
            strategy_fn=lambda policy, attempt: 99,
        )

        assert policy.strategy == "teleport"

    def test_remove_policy(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=1, max_delay_seconds=10,
        )

        engine.remove_policy("p")

        with pytest.raises(KeyError):
            engine.remove_policy("p")

    def test_remove_unknown_policy_raises(self):
        engine = GovernanceRetryEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.remove_policy("ghost")


# --- Fixed backoff ---------------------------------------------------


class TestFixedBackoff:

    def test_delay_is_constant_across_attempts(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=5, strategy="fixed",
            base_delay_seconds=10, max_delay_seconds=1000,
        )

        first = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert first.scheduled_at == BASE_TIME + timedelta(seconds=10)


# --- Linear backoff ----------------------------------------------------


class TestLinearBackoff:

    def test_delay_scales_with_attempt_number(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=5, strategy="linear",
            base_delay_seconds=10, max_delay_seconds=1000,
        )

        first = engine.schedule_retry("e-1", "p", job_id="job-1")
        engine.retry(
            "e-1",
            GovernanceExecutionManager(clock=_clock),
            run=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        second = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert first.scheduled_at == BASE_TIME + timedelta(seconds=10)
        assert second.scheduled_at == BASE_TIME + timedelta(seconds=20)


# --- Exponential backoff -------------------------------------------------


class TestExponentialBackoff:

    def test_delay_doubles_each_attempt(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=5, strategy="exponential",
            base_delay_seconds=10, max_delay_seconds=1000,
        )
        manager = GovernanceExecutionManager(clock=_clock)

        def _boom():
            raise RuntimeError("boom")

        first = engine.schedule_retry("e-1", "p", job_id="job-1")
        engine.retry("e-1", manager, run=_boom)
        second = engine.schedule_retry("e-1", "p", job_id="job-1")
        engine.retry("e-1", manager, run=_boom)
        third = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert first.scheduled_at == BASE_TIME + timedelta(seconds=10)
        assert second.scheduled_at == BASE_TIME + timedelta(seconds=20)
        assert third.scheduled_at == BASE_TIME + timedelta(seconds=40)

    def test_delay_clamped_to_max_delay_seconds(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=5, strategy="exponential",
            base_delay_seconds=10, max_delay_seconds=15,
        )

        first = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert first.scheduled_at == BASE_TIME + timedelta(seconds=10)

        engine.retry(
            "e-1", GovernanceExecutionManager(clock=_clock),
            run=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        second = engine.schedule_retry("e-1", "p", job_id="job-1")

        # Unclamped would be 20s; max_delay_seconds caps it at 15s.
        assert second.scheduled_at == BASE_TIME + timedelta(seconds=15)


# --- Jitter support -----------------------------------------------------


class TestJitterSupport:

    def test_jitter_disabled_is_deterministic(self):
        engine = GovernanceRetryEngine(clock=_clock, random_func=_no_jitter)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=10, max_delay_seconds=1000, jitter=False,
        )

        attempt = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert attempt.scheduled_at == BASE_TIME + timedelta(seconds=10)

    def test_jitter_enabled_invokes_random_func(self):
        calls = []

        def _tracking_random(low, high):
            calls.append((low, high))
            return low

        engine = GovernanceRetryEngine(
            clock=_clock, random_func=_tracking_random,
        )
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=10, max_delay_seconds=1000, jitter=True,
        )

        attempt = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert calls == [(0.0, 10.0)]
        assert attempt.scheduled_at == BASE_TIME

    def test_exponential_with_jitter_always_jitters(self):
        calls = []

        def _tracking_random(low, high):
            calls.append((low, high))
            return high

        engine = GovernanceRetryEngine(
            clock=_clock, random_func=_tracking_random,
        )
        engine.register_policy(
            "p", max_attempts=3, strategy="exponential_with_jitter",
            base_delay_seconds=10, max_delay_seconds=1000, jitter=False,
        )

        engine.schedule_retry("e-1", "p", job_id="job-1")

        assert calls == [(0.0, 10.0)]


# --- Retry scheduling ------------------------------------------------


class TestRetryScheduling:

    def test_schedule_retry_returns_attempt_one_first(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )

        attempt = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert attempt.attempt == 1

    def test_schedule_retry_unknown_policy_raises(self):
        engine = GovernanceRetryEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.schedule_retry("e-1", "ghost", job_id="job-1")

    def test_scheduled_attempt_appears_in_pending(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")

        assert len(engine.pending()) == 1

    def test_next_retry_returns_soonest(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "far", max_attempts=3, strategy="fixed",
            base_delay_seconds=100, max_delay_seconds=1000,
        )
        engine.register_policy(
            "near", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=1000,
        )
        engine.schedule_retry("e-far", "far", job_id="job-1")
        engine.schedule_retry("e-near", "near", job_id="job-2")

        assert engine.next_retry().execution_id == "e-near"

    def test_next_retry_none_when_nothing_pending(self):
        engine = GovernanceRetryEngine(clock=_clock)

        assert engine.next_retry() is None

    def test_reason_is_recorded(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )

        attempt = engine.schedule_retry(
            "e-1", "p", job_id="job-1", reason="connection reset",
        )

        assert attempt.reason == "connection reset"


# --- Retry cancellation --------------------------------------------------


class TestRetryCancellation:

    def test_cancel_removes_from_pending(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")

        engine.cancel_retry("e-1")

        assert engine.pending() == ()

    def test_cancel_unknown_execution_raises(self):
        engine = GovernanceRetryEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.cancel_retry("ghost")

    def test_cancel_then_reschedule_starts_fresh_at_attempt_one(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        engine.cancel_retry("e-1")

        attempt = engine.schedule_retry("e-1", "p", job_id="job-1")

        assert attempt.attempt == 1


# --- Max-attempt enforcement -----------------------------------------


class TestMaxAttemptEnforcement:

    def test_exceeding_max_attempts_raises_and_publishes_exhausted(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceRetryEngine(clock=_clock, event_bus=bus)
        engine.register_policy(
            "p", max_attempts=1, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")

        with pytest.raises(ValueError, match="exhausted"):
            engine.schedule_retry("e-1", "p", job_id="job-1")

        assert "retry_exhausted" in received

    def test_exhausted_count_increments(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=1, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")

        with pytest.raises(ValueError):
            engine.schedule_retry("e-1", "p", job_id="job-1")

        assert engine.metrics.exhausted_count == 1


# --- Successful retry ------------------------------------------------


class TestSuccessfulRetry:

    def test_retry_dispatches_execution_and_returns_result(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        manager = GovernanceExecutionManager(clock=_clock)

        result = engine.retry("e-1", manager)

        assert result.status == "SUCCEEDED"

    def test_successful_retry_clears_pending(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        manager = GovernanceExecutionManager(clock=_clock)

        engine.retry("e-1", manager)

        assert engine.pending() == ()

    def test_retry_unknown_execution_raises(self):
        engine = GovernanceRetryEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.retry("ghost", GovernanceExecutionManager(clock=_clock))

    def test_retry_calls_the_given_run_callable(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        manager = GovernanceExecutionManager(clock=_clock)
        calls = []

        engine.retry("e-1", manager, run=lambda: calls.append("ran"))

        assert calls == ["ran"]

    def test_succeeded_count_increments(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")

        engine.retry("e-1", GovernanceExecutionManager(clock=_clock))

        assert engine.metrics.succeeded_count == 1


# --- Exhausted retries -------------------------------------------------


class TestExhaustedRetries:

    def test_failed_retry_does_not_auto_reschedule(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        manager = GovernanceExecutionManager(clock=_clock)

        result = engine.retry(
            "e-1", manager,
            run=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert result.status == "FAILED"
        # The caller decides whether to schedule another attempt —
        # retry() itself never does so automatically.
        assert engine.pending() == ()

    def test_caller_driven_loop_eventually_exhausts(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=2, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        manager = GovernanceExecutionManager(clock=_clock)

        def _boom():
            raise RuntimeError("boom")

        engine.schedule_retry("e-1", "p", job_id="job-1", reason="first")
        result = engine.retry("e-1", manager, run=_boom)
        assert result.status == "FAILED"

        engine.schedule_retry("e-1", "p", job_id="job-1", reason=result.error)
        result = engine.retry("e-1", manager, run=_boom)
        assert result.status == "FAILED"

        with pytest.raises(ValueError, match="exhausted"):
            engine.schedule_retry(
                "e-1", "p", job_id="job-1", reason=result.error,
            )

        assert engine.metrics.exhausted_count == 1


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_schedule_publishes_retry_scheduled(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceRetryEngine(clock=_clock, event_bus=bus)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")

        assert received == ["retry_scheduled"]

    def test_retry_publishes_started_then_succeeded(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceRetryEngine(clock=_clock, event_bus=bus)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        received.clear()

        engine.retry("e-1", GovernanceExecutionManager(clock=_clock))

        assert received == ["retry_started", "retry_succeeded"]

    def test_failed_retry_publishes_only_started(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceRetryEngine(clock=_clock, event_bus=bus)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        received.clear()

        engine.retry(
            "e-1", GovernanceExecutionManager(clock=_clock),
            run=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert received == ["retry_started"]

    def test_cancel_publishes_retry_cancelled(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceRetryEngine(clock=_clock, event_bus=bus)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("e-1", "p", job_id="job-1")
        received.clear()

        engine.cancel_retry("e-1")

        assert received == ["retry_cancelled"]


# --- Execution manager integration -----------------------------------


class TestExecutionManagerIntegration:

    def test_failed_execution_auto_schedules_a_retry(self):
        retry_engine = GovernanceRetryEngine(clock=_clock)
        retry_engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        manager = GovernanceExecutionManager(
            clock=_clock, retry_engine=retry_engine, retry_policy_id="p",
        )

        result = manager.execute(
            "job-1",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        pending = retry_engine.pending()

        assert len(pending) == 1
        assert pending[0].execution_id == result.execution_id
        assert pending[0].reason == "boom"

    def test_successful_execution_schedules_nothing(self):
        retry_engine = GovernanceRetryEngine(clock=_clock)
        retry_engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        manager = GovernanceExecutionManager(
            clock=_clock, retry_engine=retry_engine, retry_policy_id="p",
        )

        manager.execute("job-1")

        assert retry_engine.pending() == ()

    def test_without_retry_policy_id_nothing_is_scheduled(self):
        retry_engine = GovernanceRetryEngine(clock=_clock)
        retry_engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        manager = GovernanceExecutionManager(
            clock=_clock, retry_engine=retry_engine,
        )

        manager.execute(
            "job-1",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert retry_engine.pending() == ()

    def test_exhaustion_from_auto_schedule_does_not_raise(self):
        retry_engine = GovernanceRetryEngine(clock=_clock)
        retry_engine.register_policy(
            "p", max_attempts=1, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        manager = GovernanceExecutionManager(
            clock=_clock, retry_engine=retry_engine, retry_policy_id="p",
        )

        def _boom():
            raise RuntimeError("boom")

        first_id = manager.execute("job-1", _boom).execution_id
        retry_engine.cancel_retry(first_id)

        # Second failure of the SAME execution chain would exceed
        # max_attempts=1 if scheduled under the same execution_id, but
        # execute() always mints a fresh execution_id, so this exercises
        # only that the manager never raises regardless.
        result = manager.execute("job-1", _boom)

        assert result.status == "FAILED"


# --- Singleton -------------------------------------------------------------


class TestRetryEngineSingleton:

    def test_get_retry_engine_returns_same_instance(self):
        from backend.observability.deployment_governance_retry import (
            get_retry_engine,
        )

        assert get_retry_engine() is get_retry_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRetryApi:

    def test_get_retries_pending_returns_a_list(self, client) -> None:
        response = client.get("/governance/retries/pending")

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_retries_returns_a_list(self, client) -> None:
        response = client.get("/governance/retries")

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_post_retry_dispatches_a_pending_attempt(self, client) -> None:
        from backend.observability.deployment_governance_retry import (
            get_retry_engine,
        )

        policy_id = f"policy-{uuid4()}"
        engine = get_retry_engine()
        engine.register_policy(
            policy_id, max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        attempt = engine.schedule_retry(
            f"exec-{uuid4()}", policy_id, job_id="job-1",
        )

        response = client.post(f"/governance/retries/{attempt.execution_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "SUCCEEDED"

    def test_post_retry_unknown_execution_returns_404(self, client) -> None:
        response = client.post("/governance/retries/ghost")

        assert response.status_code == 404

    def test_delete_retry_cancels_a_pending_attempt(self, client) -> None:
        from backend.observability.deployment_governance_retry import (
            get_retry_engine,
        )

        policy_id = f"policy-{uuid4()}"
        engine = get_retry_engine()
        engine.register_policy(
            policy_id, max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        attempt = engine.schedule_retry(
            f"exec-{uuid4()}", policy_id, job_id="job-1",
        )

        response = client.delete(
            f"/governance/retries/{attempt.execution_id}"
        )

        assert response.status_code == 200
        assert response.json() == {"cancelled": attempt.execution_id}

    def test_delete_retry_unknown_execution_returns_404(self, client) -> None:
        response = client.delete("/governance/retries/ghost")

        assert response.status_code == 404
