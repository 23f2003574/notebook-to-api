from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_trigger_engine import GovernanceTriggerEngine

# The lifecycle every execution passes through: PENDING the instant it
# is admitted (past the duplicate/concurrency checks) but before the
# job callable actually starts, RUNNING for the duration of the
# callable, then exactly one of the three terminal states.
EXECUTION_STATES: "tuple[str, ...]" = (
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
)

_TERMINAL_STATES: "frozenset[str]" = frozenset(
    {"SUCCEEDED", "FAILED", "CANCELLED"}
)


@dataclass(frozen=True)
class JobExecution:
    """
    A currently in-flight execution's identity and live status.
    """

    execution_id: str

    job_id: str

    started_at: datetime

    status: str

    def __post_init__(self) -> None:
        if not self.execution_id:
            raise ValueError("execution_id must not be empty")

        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if self.started_at.tzinfo is None:
            raise ValueError(
                "started_at must be timezone-aware"
            )

        if self.status not in EXECUTION_STATES:
            raise ValueError(
                f"status must be one of {EXECUTION_STATES}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "started_at": self.started_at.isoformat(),
            "status": self.status,
        }


@dataclass(frozen=True)
class ExecutionResult:
    """
    The immutable, terminal outcome of one execute() call.
    """

    execution_id: str

    status: str

    completed_at: datetime

    duration_ms: int

    error: "str | None"

    def __post_init__(self) -> None:
        if not self.execution_id:
            raise ValueError("execution_id must not be empty")

        if self.status not in EXECUTION_STATES:
            raise ValueError(
                f"status must be one of {EXECUTION_STATES}"
            )

        if self.status not in _TERMINAL_STATES:
            raise ValueError(
                f"status must be a terminal state: {sorted(_TERMINAL_STATES)}"
            )

        if self.completed_at.tzinfo is None:
            raise ValueError(
                "completed_at must be timezone-aware"
            )

        if self.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")

        if self.status == "FAILED" and self.error is None:
            raise ValueError(
                "error must be set when status is 'FAILED'"
            )

        if self.status != "FAILED" and self.error is not None:
            raise ValueError(
                "error must not be set unless status is 'FAILED'"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "completed_at": self.completed_at.isoformat(),
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class ExecutionManagerStatus:
    """
    A lightweight, always-current rollup of this manager's own
    execution counts — the "metrics" terminal execution summaries are
    automatically folded into as they complete, without depending on
    the separate, pre-existing deployment-trace-integrity metrics
    subsystem, which models an unrelated concept.
    """

    active_count: int

    succeeded_count: int

    failed_count: int

    cancelled_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "active_count": self.active_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "cancelled_count": self.cancelled_count,
        }


class GovernanceExecutionManager:
    """
    Owns the execution lifecycle for scheduled jobs: the Scheduler
    decides when to run, the Trigger Engine decides what is eligible,
    and this manager is the one thing that actually invokes a job
    callable and records what happened.

    execute() is synchronous — the job callable runs on the calling
    thread, inside the execute() call itself. This keeps the manager's
    behavior fully deterministic for testing (no real concurrency is
    needed to exercise "duplicate execution prevented" or "concurrent
    execution limit": a job's own callable can itself attempt a nested
    execute() call while the outer one is still active, since it is
    still on the call stack) while still modeling the real rule
    correctly for whatever eventually drives concurrent execution.

    Every terminal execution (SUCCEEDED/FAILED/CANCELLED) is published
    on the event bus — which, since GovernanceEventHistory subscribes
    to that same bus as a wildcard listener, automatically forwards
    every execution summary into event history with no further code
    here — and, if an audit_service was given, recorded in the audit
    trail as well. status() plays the analogous role for metrics: a
    live rollup of counts, updated as executions complete.

    Thread-safe: every mutation of active/history state is guarded by
    an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
        trigger_engine: "GovernanceTriggerEngine | None" = None,
        max_concurrent: int = 5,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

        self._lock = threading.Lock()

        self._active: "dict[str, JobExecution]" = {}

        self._active_job_ids: "set[str]" = set()

        self._history: "list[ExecutionResult]" = []

        self._history_by_id: "dict[str, ExecutionResult]" = {}

        self._history_job_ids: "dict[str, str]" = {}

        self._succeeded_count = 0

        self._failed_count = 0

        self._cancelled_count = 0

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._audit_service = audit_service

        self._trigger_engine = trigger_engine

        self._max_concurrent = max_concurrent

    def execute(
        self,
        job_id: str,
        run: "Callable[[], None] | None" = None,
    ) -> ExecutionResult:
        """
        Run job_id's callable (a no-op if run is omitted) and record
        exactly one execution.

        Raises ValueError if job_id already has an execution actively
        running, or if the configured maximum concurrent executions
        is already reached.

        An exception raised by run() is captured, not propagated: the
        execution is recorded FAILED with the exception's message as
        error, and the resulting ExecutionResult is returned like any
        other outcome.
        """

        run = run or (lambda: None)

        with self._lock:
            if job_id in self._active_job_ids:
                raise ValueError(
                    f"job '{job_id}' is already executing"
                )

            if len(self._active) >= self._max_concurrent:
                raise ValueError(
                    "maximum concurrent executions "
                    f"({self._max_concurrent}) reached"
                )

            execution_id = str(uuid4())
            started_at = self._clock()

            execution = JobExecution(
                execution_id=execution_id,
                job_id=job_id,
                started_at=started_at,
                status="PENDING",
            )

            self._active[execution_id] = execution
            self._active_job_ids.add(job_id)

        with self._lock:
            self._active[execution_id] = replace(
                execution, status="RUNNING"
            )

        self._publish(
            "execution_started", execution_id, {"job_id": job_id}
        )

        error: "str | None" = None
        status = "SUCCEEDED"

        try:
            run()

        except Exception as exc:
            status = "FAILED"
            error = str(exc)

        with self._lock:
            # cancel() may already have finalized this execution while
            # run() was still on the call stack; if so, its result is
            # authoritative and must not be overwritten here.
            if execution_id not in self._active:
                return self._history_by_id[execution_id]

            del self._active[execution_id]
            self._active_job_ids.discard(job_id)

        result = self._finalize(
            execution_id, job_id, started_at, status, error
        )

        return result

    def execute_batch(
        self,
        job_ids: "Iterable[str] | None" = None,
        *,
        run: "Callable[[str], None] | None" = None,
    ) -> "tuple[ExecutionResult, ...]":
        """
        Execute every job_id in job_ids, in deterministic (sorted)
        order, each via a single execute() call.

        If job_ids is omitted, it is derived from the wired trigger
        engine's currently eligible triggers (evaluate_all(), mapped
        back to job_id via list()) — completing the Scheduler Tick ->
        Trigger Engine -> Execution Manager pipeline. Returns an empty
        tuple if job_ids is omitted and no trigger engine was given.

        run, if given, is called as run(job_id) for each job (in place
        of a no-op) — a single callable parameterized by job_id rather
        than a per-job mapping, since batches are typically homogeneous
        (e.g. "run every due job the same way").

        Propagates whatever execute() itself raises (duplicate/
        concurrency rejection) for the job that triggered it; jobs
        already processed earlier in the batch keep their recorded
        results regardless.
        """

        if job_ids is None:
            job_ids = self._due_job_ids()

        run = run or (lambda job_id: None)

        return tuple(
            self.execute(job_id, lambda job_id=job_id: run(job_id))
            for job_id in sorted(job_ids)
        )

    def cancel(self, execution_id: str) -> ExecutionResult:
        """
        Cancel a currently active execution, recording it CANCELLED.

        Raises KeyError if execution_id is not currently active (it
        may never have existed, or may have already completed).
        """

        with self._lock:
            execution = self._active.pop(execution_id, None)

            if execution is None:
                raise KeyError(
                    f"execution '{execution_id}' is not active"
                )

            self._active_job_ids.discard(execution.job_id)

        return self._finalize(
            execution_id,
            execution.job_id,
            execution.started_at,
            "CANCELLED",
            None,
        )

    def status(self) -> ExecutionManagerStatus:
        """
        Return a live rollup of this manager's execution counts.
        """

        with self._lock:
            return ExecutionManagerStatus(
                active_count=len(self._active),
                succeeded_count=self._succeeded_count,
                failed_count=self._failed_count,
                cancelled_count=self._cancelled_count,
            )

    def history(
        self,
        job_id: "str | None" = None,
        limit: int = 100,
    ) -> "tuple[ExecutionResult, ...]":
        """
        Return recorded terminal execution results, newest first,
        optionally filtered to one job_id, capped at limit.
        """

        with self._lock:
            if job_id is None:
                results = list(self._history)

            else:
                results = [
                    result
                    for result in self._history
                    if self._history_job_ids.get(
                        result.execution_id
                    ) == job_id
                ]

        results.reverse()

        return tuple(results[:limit])

    def active(self) -> "tuple[JobExecution, ...]":
        """
        Return every currently active (PENDING or RUNNING) execution,
        ordered by started_at then execution_id.
        """

        with self._lock:
            executions = list(self._active.values())

        return tuple(
            sorted(
                executions,
                key=lambda execution: (
                    execution.started_at, execution.execution_id
                ),
            )
        )

    def cleanup(self) -> int:
        """
        Purge every recorded (terminal) execution result, returning
        how many were removed. Does not affect currently active
        executions or the running counts reported by status().
        """

        with self._lock:
            removed = len(self._history)
            self._history.clear()
            self._history_by_id.clear()
            self._history_job_ids.clear()

        return removed

    def _due_job_ids(self) -> "list[str]":
        if self._trigger_engine is None:
            return []

        job_ids_by_trigger = {
            trigger.trigger_id: trigger.job_id
            for trigger in self._trigger_engine.list()
        }

        return [
            job_ids_by_trigger[evaluation.trigger_id]
            for evaluation in self._trigger_engine.evaluate_all()
            if evaluation.should_run
            and evaluation.trigger_id in job_ids_by_trigger
        ]

    def _finalize(
        self,
        execution_id: str,
        job_id: str,
        started_at: datetime,
        status: str,
        error: "str | None",
    ) -> ExecutionResult:
        completed_at = self._clock()

        duration_ms = max(
            0,
            int(
                (completed_at - started_at).total_seconds() * 1000
            ),
        )

        result = ExecutionResult(
            execution_id=execution_id,
            status=status,
            completed_at=completed_at,
            duration_ms=duration_ms,
            error=error,
        )

        with self._lock:
            self._history.append(result)
            self._history_by_id[execution_id] = result
            self._history_job_ids[execution_id] = job_id

            if status == "SUCCEEDED":
                self._succeeded_count += 1

            elif status == "FAILED":
                self._failed_count += 1

            else:
                self._cancelled_count += 1

        event_type = {
            "SUCCEEDED": "execution_completed",
            "FAILED": "execution_failed",
            "CANCELLED": "execution_cancelled",
        }[status]

        payload = {"job_id": job_id, **result.to_dict()}

        self._publish(event_type, execution_id, payload)
        self._record_audit(event_type, job_id, result)

        return result

    def _record_audit(
        self, action: str, job_id: str, result: ExecutionResult
    ) -> None:
        if self._audit_service is None:
            return

        outcome = "success" if result.status == "SUCCEEDED" else "failure"

        self._audit_service.record(
            action=action,
            actor="system",
            resource=job_id,
            outcome=outcome,
            metadata=result.to_dict(),
        )

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, Any] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_execution_manager() -> (
    GovernanceExecutionManager
):
    """
    Build the process-wide governance execution manager, wired to the
    process-wide governance event bus, audit service, and trigger
    engine.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_trigger_engine import get_trigger_engine

    return GovernanceExecutionManager(
        event_bus=get_event_bus(),
        audit_service=get_audit_service(),
        trigger_engine=get_trigger_engine(),
    )


# Shared for the lifetime of the process: executions triggered through
# the API need to be visible to whatever queries the manager directly,
# which a persistence runtime built fresh per request cannot provide
# on its own.
_execution_manager = build_default_governance_execution_manager()


def get_execution_manager() -> GovernanceExecutionManager:
    """
    Return the process-wide governance execution manager.
    """

    return _execution_manager
