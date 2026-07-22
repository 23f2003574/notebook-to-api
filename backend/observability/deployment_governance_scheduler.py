from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, TYPE_CHECKING
from uuid import uuid4

from .deployment_governance_job_registry import GovernanceJobRegistry
from .deployment_governance_trigger_engine import GovernanceTriggerEngine

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_execution_manager import (
        ExecutionResult,
        GovernanceExecutionManager,
    )
    from .deployment_governance_job_dependencies import (
        GovernanceJobDependencyManager,
    )
    from .deployment_governance_scheduler_locks import (
        GovernanceSchedulerLockManager,
    )
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )


@dataclass(frozen=True)
class ScheduledJob:
    """
    A single job registered with the governance scheduler: what it is
    called, how often it recurs, and whether it is currently eligible
    to run.
    """

    job_id: str

    name: str

    interval_seconds: int

    enabled: bool

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if not self.name:
            raise ValueError("name must not be empty")

        if self.interval_seconds <= 0:
            raise ValueError(
                "interval_seconds must be greater than zero"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class SchedulerStatus:
    """
    A snapshot of the scheduler's current state: whether it is
    running, how many jobs are registered, and when the soonest
    scheduled job is next due.
    """

    running: bool

    active_jobs: int

    next_execution: "datetime | None"

    def __post_init__(self) -> None:
        if self.active_jobs < 0:
            raise ValueError("active_jobs must be >= 0")

        if (
            self.next_execution is not None
            and self.next_execution.tzinfo is None
        ):
            raise ValueError(
                "next_execution must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "active_jobs": self.active_jobs,
            "next_execution": (
                self.next_execution.isoformat()
                if self.next_execution is not None
                else None
            ),
        }


class GovernanceScheduler:
    """
    Registers, schedules, and coordinates governance jobs, replacing
    ad hoc per-caller timers with one central authority every other
    governance component can query for what is registered and when it
    is next due.

    register() adds a named job definition (rejecting a duplicate name
    the same way the underlying job registry does) and immediately
    schedules its first execution; schedule() recomputes a job's next
    execution time on demand (for example, after it has run) and
    cancel() clears a job's pending execution without unregistering
    it. jobs() and status() both order deterministically by
    (next_run, job_id) / earliest next_run, so repeated calls with no
    intervening change return identical output.

    All job metadata (name, namespace, description, enabled) is
    delegated to a GovernanceJobRegistry rather than stored here: this
    class owns only execution and timing (interval_seconds and each
    job's next scheduled run). A fresh private registry is created if
    none is given, so a standalone GovernanceScheduler in a test is
    fully self-contained.

    Eligibility itself — whether a scheduled next_run is actually due
    — is likewise delegated to a GovernanceTriggerEngine: every
    registered job gets a matching "interval" trigger, kept in sync by
    register()/unregister()/schedule(), completing the Scheduler Tick
    -> Trigger Engine -> Eligible Jobs pipeline an eventual dispatch
    loop (the execution manager) will drive. A fresh private engine is
    likewise created if none is given.

    Thread-safe: every mutation of the job registry is guarded by an
    internal lock, since jobs may be registered, cancelled, or queried
    from multiple threads (an API request thread and a dispatch loop,
    for instance) concurrently.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
        trigger_engine: "GovernanceTriggerEngine | None" = None,
        owner_id: "str | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._jobs: "dict[str, ScheduledJob]" = {}

        self._next_run: "dict[str, datetime]" = {}

        self._triggers: "dict[str, str]" = {}

        self._running = False

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._job_registry = job_registry or GovernanceJobRegistry(
            clock=self._clock
        )

        self._trigger_engine = trigger_engine or GovernanceTriggerEngine(
            clock=self._clock, job_registry=self._job_registry
        )

        # This scheduler instance's own identity when acquiring
        # distributed locks in run_due() — stable for the life of this
        # object, defaulting to a fresh UUID so two GovernanceScheduler
        # instances (e.g. two processes/nodes pointed at the same
        # lock provider) never collide by accident.
        self._owner_id = owner_id or str(uuid4())

        self._metrics = metrics

    def start(self) -> None:
        """
        Mark the scheduler running and publish "scheduler_started".

        Idempotent: calling start() while already running is a no-op
        and publishes nothing further.
        """

        with self._lock:
            if self._running:
                return

            self._running = True

        self._publish("scheduler_started", "scheduler")

    def stop(self) -> None:
        """
        Mark the scheduler stopped and publish "scheduler_stopped".

        Idempotent: calling stop() while already stopped is a no-op
        and publishes nothing further. Registered jobs are left
        untouched — stopping the scheduler pauses dispatch, it does
        not clear the registry.
        """

        with self._lock:
            if not self._running:
                return

            self._running = False

        self._publish("scheduler_stopped", "scheduler")

    def register(
        self,
        name: str,
        *,
        interval_seconds: int,
        enabled: bool = True,
        namespace: str = "default",
        description: str = "",
    ) -> ScheduledJob:
        """
        Register a new job under a fresh, unique job_id and, if
        enabled, schedule its first execution interval_seconds from
        now.

        Name/namespace metadata and duplicate-name validation are
        delegated to the job registry; this only raises ValueError if
        the registry rejects the registration (wrapping its reason,
        most commonly an already-registered name within namespace).
        """

        job_id = str(uuid4())

        result = self._job_registry.register(
            job_id,
            name,
            namespace=namespace,
            description=description,
            enabled=enabled,
        )

        if not result.accepted:
            raise ValueError(result.reason)

        registered = self._job_registry.get(job_id)

        with self._lock:
            job = ScheduledJob(
                job_id=job_id,
                name=name,
                interval_seconds=interval_seconds,
                enabled=enabled,
                created_at=registered.created_at,
            )

            self._jobs[job_id] = job

            initial_next_run = None

            if enabled:
                initial_next_run = job.created_at + timedelta(
                    seconds=interval_seconds
                )
                self._next_run[job_id] = initial_next_run

        trigger = self._trigger_engine.register(
            job_id,
            trigger_type="interval",
            next_run=initial_next_run,
            enabled=enabled,
        )

        with self._lock:
            self._triggers[job_id] = trigger.trigger_id

        self._publish(
            "job_registered", job.job_id, {"name": name}
        )

        if self._metrics is not None:
            with self._lock:
                registered_count = len(self._jobs)
                pending_count = len(self._next_run)

            self._metrics.record_schedule(
                registered_jobs=registered_count,
                pending_jobs=pending_count,
            )

        return job

    def unregister(self, job_id: str) -> None:
        """
        Remove a registered job and any pending scheduled execution
        for it, from this scheduler and the underlying job registry
        and trigger engine alike.

        Raises KeyError if job_id is not registered.
        """

        with self._lock:
            job = self._jobs.pop(job_id, None)

            if job is None:
                raise KeyError(
                    f"job '{job_id}' is not registered"
                )

            self._next_run.pop(job_id, None)
            trigger_id = self._triggers.pop(job_id, None)

        self._job_registry.unregister(job_id)

        if trigger_id is not None:
            self._trigger_engine.remove(trigger_id)

        self._publish(
            "job_unregistered", job_id, {"name": job.name}
        )

    def schedule(self, job_id: str) -> datetime:
        """
        (Re)compute job_id's next execution time as now plus its
        registered interval, apply the same next_run to its
        underlying trigger, and return it.

        Used both to advance a job past an execution that already ran
        and to reschedule a job previously cancel()'d.

        Raises KeyError if job_id is not registered.
        """

        with self._lock:
            job = self._jobs.get(job_id)

            if job is None:
                raise KeyError(
                    f"job '{job_id}' is not registered"
                )

            next_run = self._clock() + timedelta(
                seconds=job.interval_seconds
            )

            self._next_run[job_id] = next_run
            trigger_id = self._triggers.get(job_id)

        if trigger_id is not None:
            self._trigger_engine.reschedule(trigger_id, next_run)

        return next_run

    def cancel(self, job_id: str) -> None:
        """
        Clear job_id's pending scheduled execution, if any, without
        unregistering the job itself.

        Raises KeyError if job_id is not registered.
        """

        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(
                    f"job '{job_id}' is not registered"
                )

            self._next_run.pop(job_id, None)

    def jobs(self) -> "tuple[ScheduledJob, ...]":
        """
        Return every registered job, ordered by next execution time
        (jobs with no pending execution sort last) and then job_id,
        for deterministic output.
        """

        with self._lock:
            jobs = list(self._jobs.values())
            next_run = dict(self._next_run)

        sentinel = datetime.max.replace(tzinfo=timezone.utc)

        return tuple(
            sorted(
                jobs,
                key=lambda job: (
                    next_run.get(job.job_id, sentinel),
                    job.job_id,
                ),
            )
        )

    def status(self) -> SchedulerStatus:
        """
        Return the scheduler's current running state, how many jobs
        are registered, and the soonest pending next execution across
        every job (None if no job currently has one scheduled).
        """

        with self._lock:
            running = self._running
            active_jobs = len(self._jobs)
            pending = list(self._next_run.values())

        return SchedulerStatus(
            running=running,
            active_jobs=active_jobs,
            next_execution=min(pending) if pending else None,
        )

    def run_due(
        self,
        execution_manager: "GovernanceExecutionManager",
        *,
        run: "Callable[[str], None] | None" = None,
        dependency_manager: (
            "GovernanceJobDependencyManager | None"
        ) = None,
        lock_manager: (
            "GovernanceSchedulerLockManager | None"
        ) = None,
    ) -> "tuple[ExecutionResult, ...]":
        """
        The concrete Scheduler Tick -> Trigger Engine -> Dependency
        Manager -> Lock Manager -> Execution Manager pipeline: while
        this scheduler is running, evaluate this scheduler's own
        trigger engine for currently-eligible jobs, optionally filter
        out any that dependency_manager reports as not yet ready (its
        prerequisites have not all succeeded), then optionally acquire
        this scheduler's own distributed lock (its owner_id) for each
        remaining job via lock_manager — a job whose lock could not be
        acquired (another node already holds it) is skipped until the
        next tick, exactly like a not-yet-eligible or not-yet-ready
        one. Whatever is left is dispatched through execution_manager
        (in the same deterministic order execute_batch() itself
        uses), its lock released immediately after (this scheduler's
        own dispatch is synchronous, so there is no window where
        holding the lock past the call matters), and each dispatched
        job's next execution is rescheduled.

        dependency_manager and lock_manager are both entirely
        optional: omitting either preserves the exact behavior from
        before that stage existed. Neither the Dependency Manager nor
        the Lock Manager ever makes the Execution Manager itself aware
        they exist — only this method consults them, purely to decide
        what to dispatch.

        A no-op returning an empty tuple if the scheduler is not
        currently running (see start()) — dispatch is gated on the
        same on/off switch as every other scheduler-driven activity.
        """

        tick_started_at = self._clock()

        with self._lock:
            running = self._running
            job_ids = set(self._jobs)

        if not running:
            return ()

        triggers_by_id = {
            trigger.trigger_id: trigger.job_id
            for trigger in self._trigger_engine.list()
        }

        due_job_ids = [
            triggers_by_id[evaluation.trigger_id]
            for evaluation in self._trigger_engine.evaluate_all()
            if evaluation.should_run
            and triggers_by_id.get(evaluation.trigger_id) in job_ids
        ]

        if dependency_manager is not None:
            due_job_ids = [
                job_id
                for job_id in due_job_ids
                if dependency_manager.evaluate(job_id).ready
            ]

        if lock_manager is not None:
            due_job_ids = [
                job_id
                for job_id in due_job_ids
                if lock_manager.acquire(job_id, self._owner_id).acquired
            ]

        results = execution_manager.execute_batch(due_job_ids, run=run)

        if lock_manager is not None:
            for job_id in due_job_ids:
                lock_manager.release(job_id, self._owner_id)

        for job_id in due_job_ids:
            self.schedule(job_id)

        if self._metrics is not None:
            tick_duration_ms = max(
                0.0,
                (
                    self._clock() - tick_started_at
                ).total_seconds() * 1000,
            )

            self._metrics.record_dispatch(
                count=len(due_job_ids),
                active_jobs=len(due_job_ids),
                tick_duration_ms=tick_duration_ms,
            )

        return results

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


def build_default_governance_scheduler() -> GovernanceScheduler:
    """
    Build the process-wide governance scheduler, wired to the
    process-wide governance event bus (so scheduler_started/
    scheduler_stopped/job_registered/job_unregistered events are
    actually published), the process-wide governance job registry (so
    job metadata registered through the scheduler is visible to
    whatever queries the registry directly, and vice versa), and the
    process-wide governance trigger engine (so eligibility computed
    for jobs registered through the scheduler is visible to whatever
    queries the engine directly).
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_job_registry import get_job_registry
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from .deployment_governance_trigger_engine import get_trigger_engine

    return GovernanceScheduler(
        event_bus=get_event_bus(),
        job_registry=get_job_registry(),
        trigger_engine=get_trigger_engine(),
        metrics=get_scheduler_metrics(),
    )


# Shared for the lifetime of the process: registered jobs need to be
# visible to whatever queries the scheduler (the lifecycle manager's
# start/stop, or a direct API caller), which a persistence runtime
# built fresh per request cannot provide on its own.
_scheduler = build_default_governance_scheduler()


def get_scheduler() -> GovernanceScheduler:
    """
    Return the process-wide governance scheduler.
    """

    return _scheduler
