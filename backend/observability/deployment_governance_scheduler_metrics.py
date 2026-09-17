from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus

# How many recent samples each rolling-average timer keeps. A bounded
# window, not an unbounded log: this module reports moving averages
# for operational visibility, not a durable history (that is what
# GovernanceExecutionManager.history() and GovernanceRetryEngine.
# history() are already for).
_ROLLING_WINDOW = 200


@dataclass(frozen=True)
class SchedulerMetrics:
    """
    A point-in-time snapshot of the scheduler's counters and gauges.
    """

    jobs_registered: int

    jobs_scheduled: int

    jobs_completed: int

    jobs_failed: int

    jobs_cancelled: int

    active_jobs: int

    pending_jobs: int

    collected_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "jobs_registered", "jobs_scheduled", "jobs_completed",
            "jobs_failed", "jobs_cancelled", "active_jobs", "pending_jobs",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")

        if self.collected_at.tzinfo is None:
            raise ValueError(
                "collected_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "jobs_registered": self.jobs_registered,
            "jobs_scheduled": self.jobs_scheduled,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "jobs_cancelled": self.jobs_cancelled,
            "active_jobs": self.active_jobs,
            "pending_jobs": self.pending_jobs,
            "collected_at": self.collected_at.isoformat(),
        }


@dataclass(frozen=True)
class SchedulerPerformanceSnapshot:
    """
    A point-in-time snapshot of derived scheduler performance
    indicators: rolling averages and ratios computed from the
    counters/timers recorded so far.
    """

    average_execution_ms: float

    average_queue_wait_ms: float

    retry_rate: float

    scheduler_utilization: float

    collected_at: datetime

    def __post_init__(self) -> None:
        if self.average_execution_ms < 0:
            raise ValueError("average_execution_ms must be >= 0")

        if self.average_queue_wait_ms < 0:
            raise ValueError("average_queue_wait_ms must be >= 0")

        if not 0.0 <= self.retry_rate <= 1.0:
            raise ValueError("retry_rate must be between 0 and 1")

        if not 0.0 <= self.scheduler_utilization <= 1.0:
            raise ValueError(
                "scheduler_utilization must be between 0 and 1"
            )

        if self.collected_at.tzinfo is None:
            raise ValueError(
                "collected_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "average_execution_ms": self.average_execution_ms,
            "average_queue_wait_ms": self.average_queue_wait_ms,
            "retry_rate": self.retry_rate,
            "scheduler_utilization": self.scheduler_utilization,
            "collected_at": self.collected_at.isoformat(),
        }


class GovernanceSchedulerMetrics:
    """
    Operational metrics for the whole scheduling pipeline (Scheduler,
    Job Registry, Trigger Engine, Execution Manager, Retry Engine,
    Lock Manager), recorded by each of those components calling the
    matching record_*() method when it was constructed with a
    GovernanceSchedulerMetrics of its own — this module has no
    dependency on any of them, only the reverse.

    Rolling-average timers (execution_duration, queue_wait,
    retry_delay, lock_acquisition_time, scheduler_tick_duration) are
    each a bounded collections.deque: appending a new sample is a
    single, GIL-atomic operation in CPython, so recording a duration
    never needs to acquire this class's own lock — "lock-free counter
    updates where practical". Plain counters and gauges are not:
    incrementing an int (`self._x += 1`) is several bytecode
    operations, not one, so those do go through the lock. A deque's
    atomicity is a genuine, well-known CPython guarantee; claiming the
    same for a bare int increment would not be.

    snapshot()/summary() are pure reads with no side effects, so
    repeated calls with nothing recorded in between always return
    identical results ("deterministic snapshot generation").
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        slow_execution_threshold_ms: "float | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._slow_execution_threshold_ms = slow_execution_threshold_ms

        self._jobs_registered = 0
        self._jobs_executed = 0
        self._jobs_succeeded = 0
        self._jobs_failed = 0
        self._jobs_cancelled = 0
        self._jobs_retried = 0
        self._lock_contentions = 0
        self._policy_allowed = 0
        self._policy_denied = 0
        self._bootstrap_completed = 0
        self._bootstrap_failed = 0

        self._active_jobs = 0
        self._pending_jobs = 0
        self._registered_jobs = 0
        self._running_workers = 0

        self._execution_duration: "deque[float]" = deque(
            maxlen=_ROLLING_WINDOW
        )
        self._queue_wait: "deque[float]" = deque(maxlen=_ROLLING_WINDOW)
        self._retry_delay: "deque[float]" = deque(maxlen=_ROLLING_WINDOW)
        self._lock_acquisition_time: "deque[float]" = deque(
            maxlen=_ROLLING_WINDOW
        )
        self._scheduler_tick_duration: "deque[float]" = deque(
            maxlen=_ROLLING_WINDOW
        )

    def record_schedule(
        self, *, registered_jobs: int, pending_jobs: int,
    ) -> None:
        """
        Record that a job was registered with the scheduler:
        increments jobs_registered, and sets the registered_jobs/
        pending_jobs gauges to the caller's current counts (the
        caller — GovernanceScheduler — always knows these exactly;
        this class never tries to infer them independently).
        """

        with self._lock:
            self._jobs_registered += 1
            self._registered_jobs = registered_jobs
            self._pending_jobs = pending_jobs

    def record_dispatch(
        self,
        *,
        count: int = 1,
        active_jobs: "int | None" = None,
        running_workers: "int | None" = None,
        tick_duration_ms: "float | None" = None,
    ) -> None:
        """
        Record that count job(s) were dispatched for execution in one
        go (one scheduler tick can dispatch any number of due jobs at
        once — count defaults to 1 for a single dispatch, but a tick
        should pass the actual number so jobs_executed reflects real
        totals rather than one increment per tick regardless of how
        many jobs it dispatched): increments jobs_executed by count,
        and optionally updates the active_jobs/running_workers gauges
        and records this scheduler tick's duration.

        A count of 0 is valid (a tick with nothing due) and still
        optionally records tick_duration_ms, without incrementing
        jobs_executed at all.
        """

        if count < 0:
            raise ValueError("count must be >= 0")

        if tick_duration_ms is not None:
            self._scheduler_tick_duration.append(tick_duration_ms)

        with self._lock:
            self._jobs_executed += count

            if active_jobs is not None:
                self._active_jobs = active_jobs

            if running_workers is not None:
                self._running_workers = running_workers

    def record_completion(self, *, execution_ms: float) -> None:
        """
        Record a successful execution: increments jobs_succeeded and
        records execution_ms.
        """

        self._execution_duration.append(execution_ms)

        with self._lock:
            self._jobs_succeeded += 1

        self._check_execution_threshold(execution_ms)

    def record_failure(
        self, *, execution_ms: float, cancelled: bool = False,
    ) -> None:
        """
        Record an unsuccessful execution: increments jobs_cancelled if
        cancelled, otherwise jobs_failed, and records execution_ms
        either way.
        """

        self._execution_duration.append(execution_ms)

        with self._lock:
            if cancelled:
                self._jobs_cancelled += 1

            else:
                self._jobs_failed += 1

        self._check_execution_threshold(execution_ms)

    def record_retry(self, *, delay_ms: float) -> None:
        """
        Record a scheduled retry attempt: increments jobs_retried and
        records delay_ms.
        """

        self._retry_delay.append(delay_ms)

        with self._lock:
            self._jobs_retried += 1

    def record_lock_contention(
        self,
        *,
        acquisition_ms: "float | None" = None,
        contended: bool = True,
    ) -> None:
        """
        Record a lock acquisition attempt: increments lock_contentions
        if contended (the default — this method exists specifically to
        report contention), and records acquisition_ms if given,
        regardless of contended.
        """

        if acquisition_ms is not None:
            self._lock_acquisition_time.append(acquisition_ms)

        if contended:
            with self._lock:
                self._lock_contentions += 1

    def record_queue_wait(self, *, wait_ms: float) -> None:
        """
        Record how long a job waited between becoming eligible and
        actually being dispatched.
        """

        self._queue_wait.append(wait_ms)

    def record_policy_decision(self, *, allowed: bool) -> None:
        """
        Record one GovernanceSchedulerPolicyEngine decision outcome —
        added alongside that engine (which the "Update ...
        deployment_governance_scheduler_metrics.py" instruction for
        that commit exists specifically to accommodate), not part of
        the original counters this class shipped with.
        """

        with self._lock:
            if allowed:
                self._policy_allowed += 1

            else:
                self._policy_denied += 1

    def record_bootstrap(self, *, initialized: bool) -> None:
        """
        Record one GovernanceSchedulerBootstrap initialize() outcome —
        added alongside that bootstrap (the same reason
        record_policy_decision exists alongside
        GovernanceSchedulerPolicyEngine), not part of the original
        counters this class shipped with.
        """

        with self._lock:
            if initialized:
                self._bootstrap_completed += 1

            else:
                self._bootstrap_failed += 1

    @property
    def bootstrap_counts(self) -> "dict[str, int]":
        """
        Return the current tally of scheduler bootstrap outcomes.
        """

        with self._lock:
            return {
                "completed": self._bootstrap_completed,
                "failed": self._bootstrap_failed,
            }

    @property
    def policy_decisions(self) -> "dict[str, int]":
        """
        Return the current tally of scheduler policy decisions.
        """

        with self._lock:
            return {
                "allowed": self._policy_allowed,
                "denied": self._policy_denied,
            }

    def snapshot(self) -> SchedulerMetrics:
        """
        Return the current counters and gauges, publishing
        "scheduler_metrics_snapshot" with the same data.
        """

        with self._lock:
            result = SchedulerMetrics(
                jobs_registered=self._jobs_registered,
                jobs_scheduled=self._jobs_executed,
                jobs_completed=self._jobs_succeeded,
                jobs_failed=self._jobs_failed,
                jobs_cancelled=self._jobs_cancelled,
                active_jobs=self._active_jobs,
                pending_jobs=self._pending_jobs,
                collected_at=self._clock(),
            )

        self._publish("scheduler_metrics_snapshot", result.to_dict())

        return result

    def summary(self) -> SchedulerPerformanceSnapshot:
        """
        Return derived performance indicators computed from the
        rolling-average timers and counters recorded so far.
        """

        execution_samples = list(self._execution_duration)
        queue_wait_samples = list(self._queue_wait)

        with self._lock:
            jobs_executed = self._jobs_executed
            jobs_retried = self._jobs_retried
            active_jobs = self._active_jobs
            registered_jobs = self._registered_jobs

        return SchedulerPerformanceSnapshot(
            average_execution_ms=self._mean(execution_samples),
            average_queue_wait_ms=self._mean(queue_wait_samples),
            retry_rate=(
                jobs_retried / jobs_executed if jobs_executed else 0.0
            ),
            scheduler_utilization=(
                min(1.0, active_jobs / registered_jobs)
                if registered_jobs
                else 0.0
            ),
            collected_at=self._clock(),
        )

    def reset(self) -> None:
        """
        Zero every counter, gauge, and rolling-average timer, and
        publish "scheduler_metrics_reset".
        """

        self._execution_duration.clear()
        self._queue_wait.clear()
        self._retry_delay.clear()
        self._lock_acquisition_time.clear()
        self._scheduler_tick_duration.clear()

        with self._lock:
            self._jobs_registered = 0
            self._jobs_executed = 0
            self._jobs_succeeded = 0
            self._jobs_failed = 0
            self._jobs_cancelled = 0
            self._jobs_retried = 0
            self._lock_contentions = 0
            self._policy_allowed = 0
            self._policy_denied = 0
            self._bootstrap_completed = 0
            self._bootstrap_failed = 0
            self._active_jobs = 0
            self._pending_jobs = 0
            self._registered_jobs = 0
            self._running_workers = 0

        self._publish("scheduler_metrics_reset", {})

    def _check_execution_threshold(self, execution_ms: float) -> None:
        if (
            self._slow_execution_threshold_ms is not None
            and execution_ms > self._slow_execution_threshold_ms
        ):
            self._publish(
                "scheduler_metrics_threshold_exceeded",
                {
                    "execution_ms": execution_ms,
                    "threshold_ms": self._slow_execution_threshold_ms,
                },
            )

    def _mean(self, samples: "list[float]") -> float:
        if not samples:
            return 0.0

        return sum(samples) / len(samples)

    def _publish(
        self, event_type: str, payload: "dict[str, object]"
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="scheduler_metrics", payload=payload
        )


def build_default_governance_scheduler_metrics() -> (
    GovernanceSchedulerMetrics
):
    """
    Build the process-wide governance scheduler metrics collector,
    wired to the process-wide governance event bus.
    """

    from .deployment_governance_event_bus import get_event_bus

    return GovernanceSchedulerMetrics(event_bus=get_event_bus())


# Shared for the lifetime of the process: every component instrumented
# with metrics recording (Scheduler, Execution Manager, Retry Engine,
# Lock Manager) needs to record into the same collector for
# GET /governance/scheduler/metrics to reflect the whole pipeline, not
# just whichever component happened to build its own.
_scheduler_metrics = build_default_governance_scheduler_metrics()


def get_scheduler_metrics() -> GovernanceSchedulerMetrics:
    """
    Return the process-wide governance scheduler metrics collector.
    """

    return _scheduler_metrics
