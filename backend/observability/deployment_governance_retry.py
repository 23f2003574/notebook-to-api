from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_execution_manager import (
        ExecutionResult,
        GovernanceExecutionManager,
    )
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )

# The backoff strategies this engine understands natively. A custom
# strategy not in this tuple may still be registered by passing an
# explicit strategy_fn callable to register_policy(), the same
# plug-in point GovernanceRecoveryManager and GovernanceTriggerEngine
# offer for their own respective built-in sets.
BUILT_IN_BACKOFF_STRATEGIES: "tuple[str, ...]" = (
    "fixed",
    "linear",
    "exponential",
    "exponential_with_jitter",
)

BackoffStrategyFn = Callable[["RetryPolicy", int], float]


@dataclass(frozen=True)
class RetryPolicy:
    """
    A named, registered retry policy: how many attempts to allow, which
    backoff strategy computes the delay between them, and the bounds
    that clamp it.
    """

    policy_id: str

    max_attempts: int

    strategy: str

    base_delay_seconds: float

    max_delay_seconds: float

    jitter: bool

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("policy_id must not be empty")

        if not self.strategy:
            raise ValueError("strategy must not be empty")

        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")

        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "max_attempts": self.max_attempts,
            "strategy": self.strategy,
            "base_delay_seconds": self.base_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
            "jitter": self.jitter,
        }


@dataclass(frozen=True)
class RetryAttempt:
    """
    One scheduled retry attempt for a given (originating) execution_id
    — the execution_id identifies the whole retry chain, not
    necessarily the specific execution row a later attempt eventually
    produces through the execution manager, since re-running a job
    always mints a fresh execution_id of its own.
    """

    execution_id: str

    attempt: int

    scheduled_at: datetime

    reason: "str | None"

    def __post_init__(self) -> None:
        if not self.execution_id:
            raise ValueError("execution_id must not be empty")

        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")

        if self.scheduled_at.tzinfo is None:
            raise ValueError(
                "scheduled_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_id": self.execution_id,
            "attempt": self.attempt,
            "scheduled_at": self.scheduled_at.isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RetryMetrics:
    """
    A live rollup of this engine's own retry counters — the governance
    metrics this engine is responsible for keeping current, without
    depending on the separate, pre-existing deployment-trace-integrity
    metrics subsystem, which models an unrelated concept (mirrors
    GovernanceExecutionManager's own ExecutionManagerStatus rollup).
    """

    scheduled_count: int

    succeeded_count: int

    exhausted_count: int

    @property
    def success_rate(self) -> float:
        if self.scheduled_count == 0:
            return 0.0

        return self.succeeded_count / self.scheduled_count

    def to_dict(self) -> dict[str, object]:
        return {
            "scheduled_count": self.scheduled_count,
            "succeeded_count": self.succeeded_count,
            "exhausted_count": self.exhausted_count,
            "success_rate": self.success_rate,
        }


class GovernanceRetryEngine:
    """
    Recovers failed job executions using configurable retry policies
    and backoff strategies, extending the Execution Manager without
    being coupled to it: this engine never inspects execution history
    itself (retry state is entirely its own, independent bookkeeping)
    and only ever touches an execution_manager when a caller explicitly
    passes one to retry().

    schedule_retry() is the entry point after a failure: it computes
    the next attempt number for execution_id purely from its own
    internal counters (never by querying the execution manager),
    applies the policy's backoff strategy to compute when that attempt
    is due, and records it as pending. If the next attempt would
    exceed the policy's max_attempts, it instead publishes
    "retry_exhausted" and raises, scheduling nothing.

    retry() is the dispatch point: it consumes execution_id's pending
    attempt, publishes "retry_started", and drives exactly one
    execution_manager.execute() call — publishing "retry_succeeded"
    (and forgetting the chain) if that succeeds, or simply returning
    the failed result otherwise. Deciding whether to call
    schedule_retry() again for another attempt is left to the caller,
    the same way GovernanceExecutionManager.execute() never decides on
    its own to retry a failure.

    Thread-safe: every mutation of the pending retry queue is guarded
    by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        random_func: Callable[[float, float], float] | None = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._policies: "dict[str, RetryPolicy]" = {}

        self._strategy_fns: "dict[str, BackoffStrategyFn]" = {}

        self._attempt_counts: "dict[str, int]" = {}

        self._pending: "dict[str, RetryAttempt]" = {}

        self._job_ids: "dict[str, str]" = {}

        self._policy_ids: "dict[str, str]" = {}

        self._history: "list[RetryAttempt]" = []

        self._scheduled_count = 0

        self._succeeded_count = 0

        self._exhausted_count = 0

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._random = random_func or random.uniform

        self._metrics = metrics

    def register_policy(
        self,
        policy_id: str,
        *,
        max_attempts: int,
        strategy: str,
        base_delay_seconds: float,
        max_delay_seconds: float,
        jitter: bool = False,
        strategy_fn: "BackoffStrategyFn | None" = None,
    ) -> RetryPolicy:
        """
        Register a new retry policy under policy_id.

        If strategy_fn is omitted, strategy must be one of
        BUILT_IN_BACKOFF_STRATEGIES; if given, it is used regardless
        of what strategy is named (a custom pluggable strategy).

        Raises ValueError if policy_id is already registered, or if no
        strategy_fn was given and strategy is not a built-in.
        """

        with self._lock:
            if policy_id in self._policies:
                raise ValueError(
                    f"policy '{policy_id}' is already registered"
                )

            if strategy_fn is None:
                strategy_fn = self._built_in_strategies().get(strategy)

                if strategy_fn is None:
                    raise ValueError(
                        f"unknown backoff strategy '{strategy}'; pass "
                        "an explicit strategy_fn for a custom strategy"
                    )

            policy = RetryPolicy(
                policy_id=policy_id,
                max_attempts=max_attempts,
                strategy=strategy,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                jitter=jitter,
            )

            self._policies[policy_id] = policy
            self._strategy_fns[policy_id] = strategy_fn

        return policy

    def remove_policy(self, policy_id: str) -> None:
        """
        Remove a registered retry policy.

        Raises KeyError if policy_id is not registered. Does not
        affect any retry chain already in flight under that policy.
        """

        with self._lock:
            if policy_id not in self._policies:
                raise KeyError(
                    f"policy '{policy_id}' is not registered"
                )

            del self._policies[policy_id]
            del self._strategy_fns[policy_id]

    def schedule_retry(
        self,
        execution_id: str,
        policy_id: str,
        *,
        job_id: str,
        reason: "str | None" = None,
    ) -> RetryAttempt:
        """
        Schedule execution_id's next retry attempt under policy_id.

        The attempt number is entirely this engine's own bookkeeping —
        one more than however many attempts have already been
        scheduled for execution_id — never derived from querying an
        execution manager's history.

        Raises KeyError if policy_id is not registered. Raises
        ValueError, after publishing "retry_exhausted", if the next
        attempt would exceed the policy's max_attempts.
        """

        with self._lock:
            policy = self._policies.get(policy_id)

            if policy is None:
                raise KeyError(
                    f"policy '{policy_id}' is not registered"
                )

            attempt = self._attempt_counts.get(execution_id, 0) + 1
            strategy_fn = self._strategy_fns[policy_id]

        if attempt > policy.max_attempts:
            with self._lock:
                self._exhausted_count += 1

            self._publish(
                "retry_exhausted",
                execution_id,
                {"job_id": job_id, "max_attempts": policy.max_attempts},
            )

            raise ValueError(
                f"execution '{execution_id}' has exhausted its "
                f"{policy.max_attempts} retry attempt(s)"
            )

        delay = self._compute_delay(policy, strategy_fn, attempt)

        scheduled_at = self._clock() + timedelta(seconds=delay)

        retry_attempt = RetryAttempt(
            execution_id=execution_id,
            attempt=attempt,
            scheduled_at=scheduled_at,
            reason=reason,
        )

        with self._lock:
            self._attempt_counts[execution_id] = attempt
            self._pending[execution_id] = retry_attempt
            self._job_ids[execution_id] = job_id
            self._policy_ids[execution_id] = policy_id
            self._history.append(retry_attempt)
            self._scheduled_count += 1

        self._publish(
            "retry_scheduled",
            execution_id,
            {
                "job_id": job_id,
                "attempt": attempt,
                "scheduled_at": scheduled_at.isoformat(),
            },
        )

        if self._metrics is not None:
            self._metrics.record_retry(delay_ms=delay * 1000)

        return retry_attempt

    def next_retry(self) -> "RetryAttempt | None":
        """
        Return the soonest-due pending retry attempt across every
        execution_id, without consuming it, or None if nothing is
        pending.
        """

        with self._lock:
            pending = list(self._pending.values())

        if not pending:
            return None

        return min(
            pending,
            key=lambda attempt: (attempt.scheduled_at, attempt.execution_id),
        )

    def retry(
        self,
        execution_id: str,
        execution_manager: "GovernanceExecutionManager",
        run: "Callable[[], None] | None" = None,
    ) -> "ExecutionResult":
        """
        Consume execution_id's pending retry attempt and dispatch
        exactly one execution_manager.execute() call for its job.

        Publishes "retry_started" before dispatching, and
        "retry_succeeded" (forgetting this retry chain entirely) if
        the resulting execution succeeds. On failure, simply returns
        the failed ExecutionResult — scheduling another attempt (via
        schedule_retry()) is left to the caller, exactly like
        execution_manager.execute() never decides on its own to retry.

        Raises KeyError if execution_id has no pending retry attempt.
        """

        with self._lock:
            attempt = self._pending.pop(execution_id, None)

            if attempt is None:
                raise KeyError(
                    f"execution '{execution_id}' has no pending retry"
                )

            job_id = self._job_ids[execution_id]

        self._publish(
            "retry_started",
            execution_id,
            {"job_id": job_id, "attempt": attempt.attempt},
        )

        result = execution_manager.execute(job_id, run)

        if result.status == "SUCCEEDED":
            with self._lock:
                self._succeeded_count += 1
                self._job_ids.pop(execution_id, None)
                self._policy_ids.pop(execution_id, None)
                self._attempt_counts.pop(execution_id, None)

            self._publish(
                "retry_succeeded",
                execution_id,
                {"job_id": job_id, "attempt": attempt.attempt},
            )

        return result

    def cancel_retry(self, execution_id: str) -> None:
        """
        Cancel execution_id's entire retry chain: its pending attempt
        (if any) and all associated bookkeeping.

        Raises KeyError if execution_id has no pending retry attempt.
        """

        with self._lock:
            pending = self._pending.pop(execution_id, None)

            if pending is None:
                raise KeyError(
                    f"execution '{execution_id}' has no pending retry"
                )

            job_id = self._job_ids.pop(execution_id, None)
            self._policy_ids.pop(execution_id, None)
            self._attempt_counts.pop(execution_id, None)

        self._publish(
            "retry_cancelled", execution_id, {"job_id": job_id}
        )

    def pending_context(
        self, execution_id: str
    ) -> "tuple[str, str] | None":
        """
        Return (job_id, policy_id) for a currently-pending retry
        execution_id, or None if it has none.

        A support accessor for GovernanceJobPersistence: RetryAttempt
        itself intentionally carries neither field (a caller of
        schedule_retry()/retry() already knows its own job_id and
        policy_id without needing them echoed back), but persisting
        and later restoring a pending retry chain needs both, so this
        exists purely to make that round-trip possible without
        widening RetryAttempt's own public shape.
        """

        with self._lock:
            if execution_id not in self._pending:
                return None

            job_id = self._job_ids.get(execution_id)
            policy_id = self._policy_ids.get(execution_id)

        if job_id is None or policy_id is None:
            return None

        return job_id, policy_id

    def pending(self) -> "tuple[RetryAttempt, ...]":
        """
        Return every currently pending retry attempt, ordered by
        (scheduled_at, execution_id) for deterministic output.
        """

        with self._lock:
            pending = list(self._pending.values())

        return tuple(
            sorted(
                pending,
                key=lambda attempt: (
                    attempt.scheduled_at, attempt.execution_id
                ),
            )
        )

    def history(self, limit: int = 100) -> "tuple[RetryAttempt, ...]":
        """
        Return every retry attempt ever scheduled (pending or already
        consumed), newest first, capped at limit.
        """

        with self._lock:
            results = list(self._history)

        results.reverse()

        return tuple(results[:limit])

    @property
    def metrics(self) -> RetryMetrics:
        """
        Return a live rollup of this engine's retry counters.
        """

        with self._lock:
            return RetryMetrics(
                scheduled_count=self._scheduled_count,
                succeeded_count=self._succeeded_count,
                exhausted_count=self._exhausted_count,
            )

    def _compute_delay(
        self,
        policy: RetryPolicy,
        strategy_fn: BackoffStrategyFn,
        attempt: int,
    ) -> float:
        delay = max(0.0, strategy_fn(policy, attempt))
        delay = min(delay, policy.max_delay_seconds)

        if policy.jitter or policy.strategy == "exponential_with_jitter":
            delay = self._random(0.0, delay)

        return delay

    def _built_in_strategies(self) -> "dict[str, BackoffStrategyFn]":
        return {
            "fixed": self._strategy_fixed,
            "linear": self._strategy_linear,
            "exponential": self._strategy_exponential,
            "exponential_with_jitter": self._strategy_exponential,
        }

    def _strategy_fixed(self, policy: RetryPolicy, attempt: int) -> float:
        return policy.base_delay_seconds

    def _strategy_linear(self, policy: RetryPolicy, attempt: int) -> float:
        return policy.base_delay_seconds * attempt

    def _strategy_exponential(
        self, policy: RetryPolicy, attempt: int
    ) -> float:
        return policy.base_delay_seconds * (2 ** (attempt - 1))

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_retry_engine() -> GovernanceRetryEngine:
    """
    Build the process-wide governance retry engine, wired to the
    process-wide governance event bus.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    return GovernanceRetryEngine(
        event_bus=get_event_bus(), metrics=get_scheduler_metrics(),
    )


# Shared for the lifetime of the process: retries scheduled through
# the execution manager need to be visible to whatever queries the
# engine directly, which a persistence runtime built fresh per request
# cannot provide on its own.
_retry_engine = build_default_governance_retry_engine()


def get_retry_engine() -> GovernanceRetryEngine:
    """
    Return the process-wide governance retry engine.
    """

    return _retry_engine
