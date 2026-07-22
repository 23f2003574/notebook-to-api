from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from .deployment_governance_scheduler import SchedulerStatus
from .deployment_governance_scheduler_metrics import (
    SchedulerMetrics,
    SchedulerPerformanceSnapshot,
)

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_execution_manager import (
        ExecutionResult,
        GovernanceExecutionManager,
    )
    from .deployment_governance_job_registry import (
        GovernanceJob,
        GovernanceJobRegistry,
    )
    from .deployment_governance_retry import (
        GovernanceRetryEngine,
        RetryAttempt,
    )
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_scheduler_locks import (
        GovernanceSchedulerLockManager,
        SchedulerLock,
    )
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )
    from .deployment_governance_scheduler_bootstrap import (
        GovernanceSchedulerBootstrap,
        SchedulerBootstrapStatus,
    )

_EMPTY_SCHEDULER_STATUS = SchedulerStatus(
    running=False, active_jobs=0, next_execution=None
)


@dataclass(frozen=True)
class SchedulerDashboard:
    """
    A single, UI-friendly aggregate of the whole scheduling pipeline's
    current state, generated fresh on every dashboard()/refresh() call
    — never cached, never mutated once returned.
    """

    generated_at: datetime

    scheduler: SchedulerStatus

    metrics: SchedulerMetrics

    active_jobs: int

    pending_jobs: int

    running_jobs: int

    failed_jobs: int

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError(
                "generated_at must be timezone-aware"
            )

        for field_name in (
            "active_jobs", "pending_jobs", "running_jobs", "failed_jobs",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "scheduler": self.scheduler.to_dict(),
            "metrics": self.metrics.to_dict(),
            "active_jobs": self.active_jobs,
            "pending_jobs": self.pending_jobs,
            "running_jobs": self.running_jobs,
            "failed_jobs": self.failed_jobs,
        }


@dataclass(frozen=True)
class SchedulerDashboardSummary:
    """
    A compact, top-line view of the dashboard: the handful of numbers
    someone glancing at an operations screen actually wants first.
    """

    healthy: bool

    total_jobs: int

    utilization: float

    success_rate: float

    next_execution: "datetime | None"

    def __post_init__(self) -> None:
        if self.total_jobs < 0:
            raise ValueError("total_jobs must be >= 0")

        if not 0.0 <= self.utilization <= 1.0:
            raise ValueError("utilization must be between 0 and 1")

        if not 0.0 <= self.success_rate <= 1.0:
            raise ValueError("success_rate must be between 0 and 1")

        if (
            self.next_execution is not None
            and self.next_execution.tzinfo is None
        ):
            raise ValueError(
                "next_execution must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "total_jobs": self.total_jobs,
            "utilization": self.utilization,
            "success_rate": self.success_rate,
            "next_execution": (
                self.next_execution.isoformat()
                if self.next_execution is not None
                else None
            ),
        }


class GovernanceSchedulerDashboard:
    """
    A read-only aggregation service sitting above the Scheduler, Job
    Registry, Trigger Engine, Execution Manager, Retry Engine, Lock
    Manager, and Scheduler Metrics: every method here only ever calls
    an already-public, already-read-only accessor on one of those
    (status()/list()/active()/history()/pending()/snapshot()/
    summary()) and combines the results — it never registers,
    schedules, acquires, executes, or otherwise mutates anything.

    "Thread-safe reads" here means what it can honestly mean for a
    class with no mutable state of its own: each individual piece of
    data (a scheduler status, a metrics snapshot, a list of jobs) is
    read via one call to its owning component's own already
    thread-safe accessor. There is no cross-component transaction
    making the *whole* dashboard a single atomic unit — two concurrent
    dashboard() calls can legitimately see slightly different data if
    something changes between their individual calls into the
    underlying components, the same way any two independent metrics
    scrapes of a live system can.

    Every constructor dependency is optional, matching
    GovernanceJobPersistence's own pattern: a component that was not
    wired simply contributes its "nothing here yet" default instead of
    raising.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
        execution_manager: "GovernanceExecutionManager | None" = None,
        retry_engine: "GovernanceRetryEngine | None" = None,
        lock_manager: "GovernanceSchedulerLockManager | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        bootstrap: "GovernanceSchedulerBootstrap | None" = None,
    ) -> None:
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._scheduler = scheduler

        self._job_registry = job_registry

        self._execution_manager = execution_manager

        self._retry_engine = retry_engine

        self._lock_manager = lock_manager

        self._metrics = metrics

        self._bootstrap = bootstrap

    def bootstrap_status(self) -> "SchedulerBootstrapStatus | None":
        """
        Return the wired GovernanceSchedulerBootstrap's current status,
        or None if this dashboard was not given one.

        Not wired by build_default_governance_scheduler_dashboard():
        the bootstrap singleton is what wires *this* dashboard in (as
        the pipeline's final "Expose Dashboard" step), so wiring the
        reverse reference here too would make each module's default
        factory depend on the other still being mid-construction the
        first time either is imported. A caller that wants both wired
        together passes bootstrap explicitly.
        """

        if self._bootstrap is None:
            return None

        return self._bootstrap.status()

    def dashboard(self) -> SchedulerDashboard:
        """
        Aggregate every section into one SchedulerDashboard snapshot,
        publishing "scheduler_dashboard_generated".
        """

        return self._build(event_type="scheduler_dashboard_generated")

    def refresh(self) -> SchedulerDashboard:
        """
        Identical aggregation to dashboard() — there is no cached
        state to invalidate, since nothing here is ever cached in the
        first place — but publishes "scheduler_dashboard_refreshed"
        instead, for a caller that means "I explicitly asked for the
        latest view" rather than an incidental read.
        """

        return self._build(event_type="scheduler_dashboard_refreshed")

    def summary(self) -> SchedulerDashboardSummary:
        """
        Return the compact top-line summary derived from dashboard()
        and the metrics performance summary.
        """

        dashboard = self.dashboard()
        performance = self._performance_summary()

        total_jobs = (
            len(self._job_registry.list())
            if self._job_registry is not None
            else 0
        )

        total_finished = (
            dashboard.metrics.jobs_completed
            + dashboard.metrics.jobs_failed
            + dashboard.metrics.jobs_cancelled
        )

        success_rate = (
            dashboard.metrics.jobs_completed / total_finished
            if total_finished
            else 0.0
        )

        healthy = (
            dashboard.scheduler.running
            and dashboard.metrics.jobs_completed
            >= dashboard.metrics.jobs_failed
        )

        return SchedulerDashboardSummary(
            healthy=healthy,
            total_jobs=total_jobs,
            utilization=performance.scheduler_utilization,
            success_rate=success_rate,
            next_execution=dashboard.scheduler.next_execution,
        )

    def jobs(self) -> "tuple[GovernanceJob, ...]":
        """
        Return every registered job (GovernanceJobRegistry.list()'s
        own deterministic namespace/name/job_id order).
        """

        if self._job_registry is None:
            return ()

        return self._job_registry.list()

    def executions(self) -> "tuple[ExecutionResult, ...]":
        """
        Return recorded execution history, newest first
        (GovernanceExecutionManager.history()'s own order).
        """

        if self._execution_manager is None:
            return ()

        return self._execution_manager.history()

    def retries(self) -> "tuple[RetryAttempt, ...]":
        """
        Return every currently pending retry attempt
        (GovernanceRetryEngine.pending()'s own deterministic order).
        """

        if self._retry_engine is None:
            return ()

        return self._retry_engine.pending()

    def locks(self) -> "tuple[SchedulerLock, ...]":
        """
        Return every currently stored lock
        (GovernanceSchedulerLockManager.list()'s own deterministic
        order).
        """

        if self._lock_manager is None:
            return ()

        return self._lock_manager.list()

    def metrics(self) -> SchedulerMetrics:
        """
        Return the current scheduler metrics snapshot.
        """

        if self._metrics is None:
            return SchedulerMetrics(
                jobs_registered=0, jobs_scheduled=0, jobs_completed=0,
                jobs_failed=0, jobs_cancelled=0, active_jobs=0,
                pending_jobs=0, collected_at=self._clock(),
            )

        return self._metrics.snapshot()

    def _build(self, *, event_type: str) -> SchedulerDashboard:
        scheduler_status = (
            self._scheduler.status()
            if self._scheduler is not None
            else _EMPTY_SCHEDULER_STATUS
        )

        metrics_snapshot = self.metrics()

        active_executions = (
            self._execution_manager.active()
            if self._execution_manager is not None
            else ()
        )

        active_jobs = len(active_executions)

        running_jobs = sum(
            1 for execution in active_executions
            if execution.status == "RUNNING"
        )

        enabled_jobs = sum(
            1 for job in self.jobs() if job.enabled
        )

        pending_jobs = max(0, enabled_jobs - active_jobs)

        dashboard = SchedulerDashboard(
            generated_at=self._clock(),
            scheduler=scheduler_status,
            metrics=metrics_snapshot,
            active_jobs=active_jobs,
            pending_jobs=pending_jobs,
            running_jobs=running_jobs,
            failed_jobs=metrics_snapshot.jobs_failed,
        )

        self._publish(event_type, dashboard.to_dict())

        return dashboard

    def _performance_summary(self) -> SchedulerPerformanceSnapshot:
        if self._metrics is None:
            return SchedulerPerformanceSnapshot(
                average_execution_ms=0.0, average_queue_wait_ms=0.0,
                retry_rate=0.0, scheduler_utilization=0.0,
                collected_at=self._clock(),
            )

        return self._metrics.summary()

    def _publish(
        self, event_type: str, payload: "dict[str, object]"
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="scheduler_dashboard", payload=payload
        )


def build_default_governance_scheduler_dashboard() -> (
    GovernanceSchedulerDashboard
):
    """
    Build the process-wide governance scheduler dashboard, wired to
    every other process-wide governance scheduling singleton.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from .deployment_governance_job_registry import get_job_registry
    from .deployment_governance_retry import get_retry_engine
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_scheduler_locks import (
        get_scheduler_lock_manager,
    )
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    return GovernanceSchedulerDashboard(
        event_bus=get_event_bus(),
        scheduler=get_scheduler(),
        job_registry=get_job_registry(),
        execution_manager=get_execution_manager(),
        retry_engine=get_retry_engine(),
        lock_manager=get_scheduler_lock_manager(),
        metrics=get_scheduler_metrics(),
    )


# Shared for the lifetime of the process: not for correctness (a fresh
# instance would aggregate identically, since this class holds no
# state of its own beyond references to other singletons), but so a
# persistence runtime built fresh per request has a single object to
# hand back, matching every other get_*() accessor in this codebase.
_scheduler_dashboard = build_default_governance_scheduler_dashboard()


def get_scheduler_dashboard() -> GovernanceSchedulerDashboard:
    """
    Return the process-wide governance scheduler dashboard.
    """

    return _scheduler_dashboard
