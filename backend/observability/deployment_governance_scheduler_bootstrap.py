from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from .deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceDependencyGraph,
)
from .deployment_governance_job_persistence import RestoreCounts

if TYPE_CHECKING:
    from .deployment_governance_diagnostics import GovernanceDiagnosticsService
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_execution_manager import (
        GovernanceExecutionManager,
    )
    from .deployment_governance_health import GovernanceHealthService
    from .deployment_governance_job_dependencies import (
        GovernanceJobDependencyManager,
    )
    from .deployment_governance_job_persistence import (
        GovernanceJobPersistence,
    )
    from .deployment_governance_job_registry import GovernanceJobRegistry
    from .deployment_governance_liveness import GovernanceLivenessService
    from .deployment_governance_readiness import GovernanceReadinessService
    from .deployment_governance_retry import GovernanceRetryEngine
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_scheduler_dashboard import (
        GovernanceSchedulerDashboard,
        SchedulerDashboard,
    )
    from .deployment_governance_scheduler_locks import (
        GovernanceSchedulerLockManager,
    )
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )
    from .deployment_governance_scheduler_policy import (
        GovernanceSchedulerPolicyEngine,
    )
    from .deployment_governance_trigger_engine import GovernanceTriggerEngine

# The scheduler bootstrap's own fixed, declarative initialization
# pipeline: job_registry and trigger_engine have no dependencies of
# their own; every later stage depends on the one immediately before
# it, matching the "Initialization Pipeline" diagram exactly (restore/
# start/dashboard are not part of this graph — they are steps
# initialize() runs *after* every one of these components validates,
# not components with their own start/stop lifecycle).
_COMPONENT_ORDER: "tuple[str, ...]" = (
    "job_registry",
    "trigger_engine",
    "dependency_manager",
    "lock_manager",
    "execution_manager",
    "retry_engine",
    "metrics",
    "policy_engine",
)

BOOTSTRAP_VERSION = "1"


@dataclass(frozen=True)
class SchedulerBootstrapReport:
    """
    The immutable outcome of one initialize()/restart() call.
    """

    started: bool

    restored_jobs: int

    restored_triggers: int

    restored_retry_queue: int

    initialized_components: "tuple[str, ...]"

    completed_at: datetime

    def __post_init__(self) -> None:
        if self.completed_at.tzinfo is None:
            raise ValueError(
                "completed_at must be timezone-aware"
            )

        for field_name in (
            "restored_jobs", "restored_triggers", "restored_retry_queue",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "started": self.started,
            "restored_jobs": self.restored_jobs,
            "restored_triggers": self.restored_triggers,
            "restored_retry_queue": self.restored_retry_queue,
            "initialized_components": list(self.initialized_components),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True)
class SchedulerBootstrapStatus:
    """
    A point-in-time snapshot of the scheduler bootstrap's own
    lifecycle state, distinct from the scheduler's own SchedulerStatus
    (running/active_jobs/next_execution): this describes whether the
    bootstrap subsystem itself has completed initialization, not what
    it has scheduled.
    """

    initialized: bool

    running: bool

    version: str

    started_at: "datetime | None"

    def __post_init__(self) -> None:
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError(
                "started_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "initialized": self.initialized,
            "running": self.running,
            "version": self.version,
            "started_at": (
                self.started_at.isoformat()
                if self.started_at is not None
                else None
            ),
        }


class GovernanceSchedulerBootstrapError(RuntimeError):
    """
    Raised when the scheduler bootstrap's own component dependency
    graph fails validation, aborting initialize() before any restore
    or start step runs — the scheduler-bootstrap-scoped equivalent of
    GovernanceBootstrapError.
    """

    def __init__(self, result: DependencyValidationResult) -> None:
        self.result = result

        details = []

        if result.missing:
            details.append(
                "missing dependencies: " + ", ".join(result.missing)
            )

        if result.cycles:
            details.append(
                "circular dependencies: "
                + "; ".join(
                    " -> ".join(cycle) for cycle in result.cycles
                )
            )

        super().__init__(
            "governance scheduler bootstrap dependency graph "
            "validation failed"
            + (f" ({'; '.join(details)})" if details else "")
        )


class GovernanceSchedulerBootstrap:
    """
    Wires every Scheduler & Job Orchestration component together
    (Job Registry, Trigger Engine, Dependency Manager, Lock Manager,
    Execution Manager, Retry Engine, Metrics, Policy Engine),
    validates that they form a complete dependency graph, restores
    persisted scheduler state, and starts the scheduler — replacing
    the previous pattern of each caller (the lifecycle manager, the
    API layer, a CLI command) independently deciding what order to
    touch these components in.

    initialize() is the two-phase entry point other bootstraps in
    this codebase (GovernanceIntegrityMetricsBootstrap) split into
    build()/initialize(); this one is single-shot instead, since every
    component it wires is already a live, already-constructed
    singleton (there is no separate "build" step that constructs
    fresh objects) — initialize() only validates the graph, restores
    persisted state, and starts the scheduler.

    validate() only considers a component present if it was actually
    wired at construction time: a None constructor argument is simply
    omitted from the graph rather than registered with a broken
    dependency, so a gap in the *middle* of the pipeline (e.g.
    dependency_manager wired but lock_manager not) still surfaces as
    a missing-dependency failure for whatever depends on the missing
    stage, exactly like a real configuration error would.

    Idempotent: calling initialize() again while already initialized
    returns the cached report from the call that actually ran, without
    repeating validate()/restore()/start() or publishing events again.
    shutdown() is symmetric: a no-op if not currently initialized.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
        trigger_engine: "GovernanceTriggerEngine | None" = None,
        dependency_manager: (
            "GovernanceJobDependencyManager | None"
        ) = None,
        lock_manager: "GovernanceSchedulerLockManager | None" = None,
        execution_manager: "GovernanceExecutionManager | None" = None,
        retry_engine: "GovernanceRetryEngine | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        policy_engine: "GovernanceSchedulerPolicyEngine | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        job_persistence: "GovernanceJobPersistence | None" = None,
        dashboard: "GovernanceSchedulerDashboard | None" = None,
        liveness_service: "GovernanceLivenessService | None" = None,
    ) -> None:
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._job_registry = job_registry
        self._trigger_engine = trigger_engine
        self._dependency_manager = dependency_manager
        self._lock_manager = lock_manager
        self._execution_manager = execution_manager
        self._retry_engine = retry_engine
        self._metrics = metrics
        self._policy_engine = policy_engine

        self._scheduler = scheduler
        self._job_persistence = job_persistence
        self._dashboard = dashboard
        self._liveness_service = liveness_service

        self._initialized = False
        self._started_at: "datetime | None" = None
        self._last_report: "SchedulerBootstrapReport | None" = None

    def validate(self) -> DependencyValidationResult:
        """
        Validate the fixed pipeline graph against which components
        were actually wired at construction time.
        """

        graph = GovernanceDependencyGraph()

        for index, name in enumerate(_COMPONENT_ORDER):
            if self._component(name) is None:
                # Not wired: omitted from the graph entirely. The
                # canonical predecessor below is computed from
                # _COMPONENT_ORDER regardless, so a stage depending on
                # *this* one still declares that dependency next
                # iteration — reported as missing, since this name was
                # never registered.
                continue

            previous = _COMPONENT_ORDER[index - 1] if index > 0 else None

            graph.register(
                name,
                dependencies=(previous,) if previous is not None else (),
            )

        return graph.validate()

    def initialize(self) -> SchedulerBootstrapReport:
        """
        Run the full initialization pipeline: validate the component
        dependency graph, restore persisted scheduler state, and
        start the scheduler.

        Raises GovernanceSchedulerBootstrapError, without touching
        restore/start/dashboard at all, if the dependency graph is
        invalid — fail-fast on a critical bootstrap error.
        """

        if self._initialized:
            return self._last_report

        self._publish("scheduler_bootstrap_started", {})

        result = self.validate()

        if not result.valid:
            self._publish(
                "scheduler_bootstrap_failed",
                {
                    "missing": list(result.missing),
                    "cycles": [list(cycle) for cycle in result.cycles],
                },
            )

            if self._metrics is not None:
                self._metrics.record_bootstrap(initialized=False)

            raise GovernanceSchedulerBootstrapError(result)

        initialized_components = tuple(
            name for name in _COMPONENT_ORDER
            if self._component(name) is not None
        )

        restore_counts = self.restore()

        self.start()

        self._initialized = True
        self._started_at = self._clock()

        report = SchedulerBootstrapReport(
            started=True,
            restored_jobs=restore_counts.jobs,
            restored_triggers=restore_counts.triggers,
            restored_retry_queue=restore_counts.pending_retries,
            initialized_components=initialized_components,
            completed_at=self._clock(),
        )

        self._last_report = report

        if self._metrics is not None:
            self._metrics.record_bootstrap(initialized=True)

        self._publish("scheduler_bootstrap_completed", report.to_dict())
        self._publish("scheduler_runtime_ready", {})

        return report

    def restore(self) -> RestoreCounts:
        """
        Restore persisted scheduler state (jobs, triggers, pending
        retries) via the wired job persistence layer.

        Returns a zeroed RestoreCounts, without error, if this
        bootstrap was not wired with a job_persistence layer — restore
        is opportunistic, not a requirement.
        """

        if self._job_persistence is None:
            return RestoreCounts(jobs=0, triggers=0, pending_retries=0)

        self._job_persistence.load()

        counts = self._job_persistence.last_restore()

        return (
            counts
            if counts is not None
            else RestoreCounts(jobs=0, triggers=0, pending_retries=0)
        )

    def start(self) -> None:
        """
        Start the wired scheduler. A no-op if none was wired.

        Deliberately does not touch the wired liveness service:
        unlike GovernanceIntegrityDeliveryRuntime (whose worker
        process has no other component sharing process liveness with
        it), this bootstrap can be restarted independently of the rest
        of the governance runtime via its own
        POST /governance/scheduler/restart endpoint — starting/
        resetting the *shared* process liveness singleton here would
        make an independent scheduler restart wrongly report the
        whole process as dead in between, even though the lifecycle
        manager's own "liveness_service" component is that singleton's
        one true owner in this process. See build_liveness_service for
        the read-only alternative.
        """

        if self._scheduler is not None:
            self._scheduler.start()

    def shutdown(self) -> None:
        """
        Save current scheduler state to durable persistence and stop
        the wired scheduler.

        A no-op if this bootstrap is not currently initialized.
        """

        if not self._initialized:
            return

        if self._job_persistence is not None:
            self._job_persistence.save()

        if self._scheduler is not None:
            self._scheduler.stop()

        self._initialized = False
        self._started_at = None

        self._publish("scheduler_runtime_shutdown", {})

    def restart(self) -> SchedulerBootstrapReport:
        """
        Shut down (if currently initialized) and run the full
        initialization pipeline again.
        """

        self.shutdown()

        return self.initialize()

    def status(self) -> SchedulerBootstrapStatus:
        """
        Return this bootstrap's current lifecycle state.
        """

        running = (
            self._scheduler.status().running
            if self._scheduler is not None
            else False
        )

        return SchedulerBootstrapStatus(
            initialized=self._initialized,
            running=running,
            version=BOOTSTRAP_VERSION,
            started_at=self._started_at,
        )

    def dashboard(self) -> "SchedulerDashboard | None":
        """
        Return the wired scheduler dashboard's current aggregate
        snapshot — the pipeline's final "Expose Dashboard" step.

        Returns None if this bootstrap was not wired with a
        dashboard.
        """

        if self._dashboard is None:
            return None

        return self._dashboard.dashboard()

    def build_health_service(self) -> "GovernanceHealthService":
        """
        Build a GovernanceHealthService with checks registered for
        this bootstrap's own lifecycle state, the wired scheduler, and
        the component dependency graph — mirroring
        GovernanceIntegrityDeliveryRuntime.build_health_service's
        shape for the (unrelated) delivery runtime.
        """

        from .deployment_governance_health import (
            GovernanceHealthService,
            dependency_graph_health_check,
            liveness_health_check,
        )

        service = GovernanceHealthService(clock=self._clock)

        service.register(
            "scheduler_bootstrap", self._check_bootstrap_health
        )

        service.register("scheduler", self._check_scheduler_health)

        service.register(
            "dependency_graph",
            lambda: dependency_graph_health_check(self.validate()),
        )

        if self._liveness_service is not None:
            service.register(
                "liveness",
                lambda: liveness_health_check(self._liveness_service),
            )

        return service

    def _check_bootstrap_health(self) -> "bool | tuple[bool, str | None]":
        if self._initialized:
            return True

        return False, "scheduler bootstrap has not been initialized"

    def _check_scheduler_health(self) -> "bool | tuple[bool, str | None]":
        if self._scheduler is None:
            return False, "scheduler is not configured"

        if self._scheduler.status().running:
            return True

        return False, "scheduler is not running"

    def build_readiness_service(self) -> "GovernanceReadinessService":
        """
        Build a GovernanceReadinessService checking whether the
        scheduler bootstrap has completed and the scheduler has
        accepted work.
        """

        from .deployment_governance_readiness import (
            GovernanceReadinessService,
        )

        service = GovernanceReadinessService(clock=self._clock)

        service.register(
            "scheduler_bootstrap", self._check_bootstrap_readiness
        )

        service.register("scheduler", self._check_scheduler_readiness)

        return service

    def _check_bootstrap_readiness(
        self,
    ) -> "bool | tuple[bool, str | None]":
        if self._initialized:
            return True

        return False, "scheduler bootstrap has not completed initialization"

    def _check_scheduler_readiness(
        self,
    ) -> "bool | tuple[bool, str | None]":
        if self._scheduler is None:
            return False, "scheduler is not configured"

        return True

    def build_liveness_service(self) -> "GovernanceLivenessService | None":
        """
        Return this bootstrap's wired liveness service, if any.

        Unlike build_health_service/build_readiness_service, this does
        not construct anything: liveness answers "is this process
        alive", which is process-wide state this bootstrap only
        references (via start()/shutdown()), never owns independently.
        """

        return self._liveness_service

    def build_diagnostics_service(self) -> "GovernanceDiagnosticsService":
        """
        Build a GovernanceDiagnosticsService reading from the wired
        scheduler and execution manager.

        registered_providers has no scheduler-bootstrap equivalent —
        it reports the wired job registry's job count instead, the
        same reuse-for-a-different-subsystem
        GovernanceIntegrityDeliveryRuntime.build_diagnostics_service
        performs for the (unrelated) persistence-runtime case.
        """

        from .deployment_governance_diagnostics import (
            GovernanceDiagnosticsService,
        )

        def _runtime_state() -> str:
            if not self._initialized:
                return "stopped"

            if self._scheduler is not None and self._scheduler.status().running:
                return "running"

            return "initialized"

        def _active_dispatches() -> int:
            if self._execution_manager is None:
                return 0

            return len(self._execution_manager.active())

        def _pending_dispatches() -> int:
            if self._scheduler is None:
                return 0

            return len(self._scheduler.pending_dispatches())

        def _registered_providers() -> int:
            if self._job_registry is None:
                return 0

            return len(self._job_registry.list())

        return GovernanceDiagnosticsService(
            runtime_state=_runtime_state,
            active_dispatches=_active_dispatches,
            pending_dispatches=_pending_dispatches,
            registered_providers=_registered_providers,
            clock=self._clock,
        )

    def _component(self, name: str) -> object:
        return {
            "job_registry": self._job_registry,
            "trigger_engine": self._trigger_engine,
            "dependency_manager": self._dependency_manager,
            "lock_manager": self._lock_manager,
            "execution_manager": self._execution_manager,
            "retry_engine": self._retry_engine,
            "metrics": self._metrics,
            "policy_engine": self._policy_engine,
        }[name]

    def _publish(
        self, event_type: str, payload: "dict[str, object]"
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="scheduler_bootstrap", payload=payload
        )


def build_default_governance_scheduler_bootstrap() -> (
    GovernanceSchedulerBootstrap
):
    """
    Build the process-wide governance scheduler bootstrap, wired to
    every process-wide Scheduler & Job Orchestration singleton.

    Deliberately does not wire a dashboard here: the dashboard module
    does not (and must not) import this module at its own top level,
    to avoid each module's default singleton factory depending on the
    other still being mid-construction the first time either is
    imported — see GovernanceSchedulerDashboard.bootstrap_status's
    docstring. A caller that wants both wired together
    (e.g. GET /governance/scheduler/bootstrap/dashboard, if ever
    added) should pass dashboard=get_scheduler_dashboard() explicitly.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from .deployment_governance_job_dependencies import (
        get_job_dependency_manager,
    )
    from .deployment_governance_job_persistence import get_job_persistence
    from .deployment_governance_job_registry import get_job_registry
    from .deployment_governance_liveness import get_liveness_service
    from .deployment_governance_retry import get_retry_engine
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_scheduler_locks import (
        get_scheduler_lock_manager,
    )
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from .deployment_governance_scheduler_policy import (
        get_scheduler_policy_engine,
    )
    from .deployment_governance_trigger_engine import get_trigger_engine

    return GovernanceSchedulerBootstrap(
        event_bus=get_event_bus(),
        job_registry=get_job_registry(),
        trigger_engine=get_trigger_engine(),
        dependency_manager=get_job_dependency_manager(),
        lock_manager=get_scheduler_lock_manager(),
        execution_manager=get_execution_manager(),
        retry_engine=get_retry_engine(),
        metrics=get_scheduler_metrics(),
        policy_engine=get_scheduler_policy_engine(),
        scheduler=get_scheduler(),
        job_persistence=get_job_persistence(),
        liveness_service=get_liveness_service(),
    )


# Shared for the lifetime of the process: whether the scheduler
# bootstrap has completed initialization needs to be visible to
# whatever queries it (the lifecycle manager's "scheduler" component,
# or a direct API caller), which a bootstrap built fresh per request
# cannot provide on its own.
_scheduler_bootstrap = build_default_governance_scheduler_bootstrap()


def get_scheduler_bootstrap() -> GovernanceSchedulerBootstrap:
    """
    Return the process-wide governance scheduler bootstrap.
    """

    return _scheduler_bootstrap
