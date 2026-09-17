from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceDependencyGraph,
)

if TYPE_CHECKING:
    from .deployment_governance_blue_green import (
        BlueGreenDeploymentEngine,
    )
    from .deployment_governance_canary import CanaryDeploymentEngine
    from .deployment_governance_event_bus import (
        EventSubscription,
        GovernanceEventBus,
    )
    from .deployment_governance_progressive_delivery import (
        ProgressiveDeliveryEngine,
    )
    from .deployment_governance_rollback import DeploymentRollbackEngine
    from .deployment_governance_rolling import RollingDeploymentEngine
    from .deployment_governance_rollout_analytics import (
        DeploymentRolloutAnalytics,
    )
    from .deployment_governance_rollout_dashboard import (
        DeploymentRolloutDashboard,
    )
    from .deployment_governance_rollout_health import (
        DeploymentRolloutHealthEngine,
    )
    from .deployment_governance_rollout_manager import (
        DeploymentRolloutManager,
    )
    from .deployment_governance_rollout_policy import (
        DeploymentRolloutPolicyEngine,
    )
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )

BOOTSTRAP_VERSION = "1"

# This bootstrap's own fixed, declarative validation graph — the same
# simplified linear-chain shape GovernanceSchedulerBootstrap's own
# _COMPONENT_ORDER uses (not the true dependency DAG these components
# actually wired across commits 1-12, which is not a simple chain —
# see e.g. deployment_governance_rollback.py's own docstring for how
# tangled it really is). This only orders *validation*, not
# construction: every component here is already a live, already-
# constructed process-wide singleton by the time this bootstrap is
# built.
_ROLLOUT_COMPONENT_ORDER: "tuple[str, ...]" = (
    "version_registry",
    "traffic_router",
    "blue_green_engine",
    "canary_engine",
    "rolling_engine",
    "progressive_engine",
    "rollout_manager",
    "rollback_engine",
    "health_engine",
    "analytics",
    "policy_engine",
    "dashboard",
)

# Spec shorthand -> the real, already-published event name it maps to.
# "traffic_updated"/"health_evaluated"/"policy_denied"/
# "analytics_updated" were never actually published under those exact
# names anywhere in commits 1-12 (the real vocabulary is
# "routing_updated", "rollout_health_evaluated",
# "rollout_policy_denied", "rollout_analytics_updated" —
# GOVERNANCE_EVENT_TYPES is the source of truth); this bootstrap
# subscribes to what is actually published, not the shorthand.
_TRACKED_EVENTS: "tuple[str, ...]" = (
    "rollout_started",
    "rollout_completed",
    "rollout_failed",
    "routing_updated",
    "rollout_health_evaluated",
    "rollback_completed",
    "rollout_policy_denied",
    "rollout_analytics_updated",
)


@dataclass(frozen=True)
class RolloutBootstrapReport:
    """
    The immutable outcome of one initialize() call.
    """

    started: bool

    initialized_components: "tuple[str, ...]"

    registered_jobs: "tuple[str, ...]"

    subscribed_events: "tuple[str, ...]"

    completed_at: datetime

    def __post_init__(self) -> None:
        if self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "started": self.started,
            "initialized_components": list(
                self.initialized_components
            ),
            "registered_jobs": list(self.registered_jobs),
            "subscribed_events": list(self.subscribed_events),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True)
class RolloutBootstrapStatus:
    """
    A point-in-time snapshot of this bootstrap's own lifecycle state,
    distinct from any individual wired component's own status.
    """

    initialized: bool

    version: str

    started_at: "datetime | None"

    def __post_init__(self) -> None:
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "initialized": self.initialized,
            "version": self.version,
            "started_at": (
                self.started_at.isoformat()
                if self.started_at is not None
                else None
            ),
        }


class DeploymentRolloutBootstrapError(RuntimeError):
    """
    Raised when this bootstrap's component dependency graph fails
    validation, aborting initialize() before any job/event
    registration runs — the rollout-subsystem-scoped equivalent of
    GovernanceSchedulerBootstrapError.
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
            "rollout bootstrap dependency graph validation failed"
            + (f" ({'; '.join(details)})" if details else "")
        )


class DeploymentRolloutBootstrap:
    """
    Completes the rollout subsystem (commits 1-12: DeploymentRollout
    Manager, DeploymentVersionRegistry, BlueGreenDeploymentEngine,
    CanaryDeploymentEngine, RollingDeploymentEngine,
    ProgressiveDeliveryEngine, DeploymentTrafficRouter,
    DeploymentRollbackEngine, DeploymentRolloutHealthEngine,
    DeploymentRolloutAnalytics, DeploymentRolloutPolicyEngine,
    DeploymentRolloutDashboard) by validating that they form a
    complete dependency graph, registering declarative scheduler
    jobs, and subscribing diagnostic event handlers — replacing the
    previous pattern of each of those 12 components' own build_
    default_* function independently wiring itself with no single
    place confirming the *whole* subsystem is coherent.

    Integration only, per this commit's own charter: every method
    here calls only already-public methods each component already
    exposed in its own commit (register()/unregister()/subscribe()/
    list()/refresh()) — nothing here re-implements rollout, health,
    traffic, or policy logic.

    initialize() is the single-shot entry point (mirroring Governance
    SchedulerBootstrap): every component it wires is already a live,
    already-constructed singleton, so there is no separate "build"
    step — initialize() only validates the graph, registers scheduler
    jobs, and subscribes event handlers. Idempotent: calling it again
    while already initialized returns the cached report from the call
    that actually ran. shutdown() is symmetric: a no-op if not
    currently initialized, otherwise it unregisters every job this
    bootstrap registered, unsubscribes every handler it subscribed,
    and asks the wired dashboard to refresh (replacing whatever it had
    cached with a clean read — the closest honest meaning of "clear
    caches" for a dashboard whose only cache is its own TTL-based
    overview(), with no separate invalidate() of its own to call).
    "flush analytics buffers" is a documented no-op: DeploymentRollout
    Analytics has no write-behind buffer to flush (every record() call
    already updates its in-memory state synchronously) — there is
    nothing to flush without either fabricating a buffer that does not
    exist or destructively clearing analytics history, which "flush"
    does not mean.

    Thread-safe: initialize()/shutdown() are both guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        rollout_manager: "DeploymentRolloutManager | None" = None,
        blue_green_engine: "BlueGreenDeploymentEngine | None" = None,
        canary_engine: "CanaryDeploymentEngine | None" = None,
        rolling_engine: "RollingDeploymentEngine | None" = None,
        progressive_engine: "ProgressiveDeliveryEngine | None" = None,
        rollback_engine: "DeploymentRollbackEngine | None" = None,
        health_engine: "DeploymentRolloutHealthEngine | None" = None,
        analytics: "DeploymentRolloutAnalytics | None" = None,
        policy_engine: "DeploymentRolloutPolicyEngine | None" = None,
        dashboard: "DeploymentRolloutDashboard | None" = None,
        job_interval_seconds: int = 60,
    ) -> None:
        self._lock = threading.RLock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus
        self._scheduler = scheduler

        self._version_registry = version_registry
        self._traffic_router = traffic_router
        self._rollout_manager = rollout_manager
        self._blue_green_engine = blue_green_engine
        self._canary_engine = canary_engine
        self._rolling_engine = rolling_engine
        self._progressive_engine = progressive_engine
        self._rollback_engine = rollback_engine
        self._health_engine = health_engine
        self._analytics = analytics
        self._policy_engine = policy_engine
        self._dashboard = dashboard

        self._job_interval_seconds = job_interval_seconds

        self._initialized = False
        self._started_at: "datetime | None" = None
        self._last_report: "RolloutBootstrapReport | None" = None

        self._job_ids: "dict[str, str]" = {}
        self._subscriptions: "list[EventSubscription]" = []
        self._last_event_at: "dict[str, datetime]" = {}

    def _component(self, name: str) -> object:
        return getattr(self, f"_{name}")

    def validate(self) -> DependencyValidationResult:
        """
        Validate the fixed pipeline graph against which components
        were actually wired at construction time — components not
        wired are simply omitted from the graph (see Governance
        SchedulerBootstrap.validate for why that still surfaces a real
        gap as "missing" rather than silently passing).
        """

        graph = GovernanceDependencyGraph()

        for index, name in enumerate(_ROLLOUT_COMPONENT_ORDER):
            if self._component(name) is None:
                continue

            previous = (
                _ROLLOUT_COMPONENT_ORDER[index - 1] if index > 0 else None
            )

            graph.register(
                name,
                dependencies=(previous,) if previous is not None else (),
            )

        return graph.validate()

    def register_services(self) -> "tuple[str, ...]":
        """
        Validate the component dependency graph, returning the tuple
        of components that were actually wired (in
        _ROLLOUT_COMPONENT_ORDER).

        Raises DeploymentRolloutBootstrapError if the graph is
        invalid — "dependency validation before startup".
        """

        result = self.validate()

        if not result.valid:
            raise DeploymentRolloutBootstrapError(result)

        return tuple(
            name for name in _ROLLOUT_COMPONENT_ORDER
            if self._component(name) is not None
        )

    def register_api(self) -> bool:
        """
        Confirm every rollout endpoint from commits 1-12 is mounted
        under "/governance".

        There is no separate route-registration step to perform here:
        every rollout endpoint is already registered, at import time,
        by deployment_governance_api.py's own module-level
        @health_router.* decorators, and that shared health_router is
        already included into the running FastAPI app
        (backend/dashboard.py's app.include_router(...)) once,
        regardless of whether this bootstrap ever runs. This is a
        verification that centralization held, not a second,
        redundant registration.

        Returns False, without raising, if deployment_governance_api's
        health_router does not carry the expected prefix — a caller
        decides what to do about that (this is a diagnostic, not
        itself a startup-blocking step, unlike register_services()).
        """

        from .deployment_governance_api import health_router

        return health_router.prefix == "/governance"

    def register_scheduler_jobs(self) -> "tuple[str, ...]":
        """
        Register one declarative job per Scheduler Jobs section
        (rollout progression, health evaluation, analytics
        aggregation, rollback trigger evaluation, dashboard cache
        refresh) on the wired scheduler.

        Idempotent: returns the already-registered job names, without
        re-registering, if called again. Returns an empty tuple,
        without error, if no scheduler is wired — matching how every
        per-strategy engine's own scheduler integration (Canary
        Deployment Engine, Rolling Deployment Engine) treats a missing
        scheduler as "nothing to declare", not a failure. These jobs
        are purely declarative, the same "something else is still
        responsible for actually invoking the work" contract those
        two engines' own scheduled jobs already carry — this bootstrap
        does not execute rollout logic on a timer, it only declares
        that the work exists.
        """

        if self._scheduler is None:
            return ()

        with self._lock:
            if self._job_ids:
                return tuple(self._job_ids)

            job_specs = (
                (
                    "rollout-progression",
                    "Periodic rollout progression sweep",
                ),
                (
                    "rollout-health-evaluation",
                    "Periodic rollout health evaluation sweep",
                ),
                (
                    "rollout-analytics-aggregation",
                    "Periodic rollout analytics aggregation",
                ),
                (
                    "rollout-rollback-trigger-evaluation",
                    "Periodic rollback trigger evaluation",
                ),
                (
                    "rollout-dashboard-cache-refresh",
                    "Periodic rollout dashboard cache refresh",
                ),
            )

            job_ids: "dict[str, str]" = {}

            for name, description in job_specs:
                job = self._scheduler.register(
                    name,
                    interval_seconds=self._job_interval_seconds,
                    namespace="rollout-bootstrap",
                    description=description,
                )

                job_ids[name] = job.job_id

            self._job_ids = job_ids

            return tuple(job_ids)

    def register_event_handlers(self) -> "tuple[str, ...]":
        """
        Subscribe a diagnostic handler (recording each event type's
        last-seen time, for health_check()) to every tracked event in
        _TRACKED_EVENTS.

        Idempotent: returns the already-subscribed event types,
        without subscribing again, if called again. Returns an empty
        tuple, without error, if no event_bus is wired. Does not
        re-implement any cross-component coordination those events
        already drive (e.g. DeploymentRollbackEngine's own
        "rollout_failed"/"rollout_health_critical" subscriptions from
        commits 8-9) — this is observational bookkeeping only.
        """

        if self._event_bus is None:
            return ()

        with self._lock:
            if self._subscriptions:
                return tuple(
                    subscription.event_type
                    for subscription in self._subscriptions
                )

            subscriptions = [
                self._event_bus.subscribe(
                    event_type, self._on_tracked_event
                )
                for event_type in _TRACKED_EVENTS
            ]

            self._subscriptions = subscriptions

            return tuple(
                subscription.event_type for subscription in subscriptions
            )

    def _on_tracked_event(self, event: Any) -> None:
        with self._lock:
            self._last_event_at[event.event_type] = self._clock()

    def initialize(self) -> RolloutBootstrapReport:
        """
        Run the full initialization pipeline: register_services() ->
        register_api() -> register_scheduler_jobs() ->
        register_event_handlers() -> Ready, matching the Runtime
        Integration diagram exactly.

        Raises DeploymentRolloutBootstrapError, without touching
        API/job/event registration at all, if the dependency graph is
        invalid — fail-fast on a critical bootstrap error, matching
        GovernanceSchedulerBootstrap.initialize.
        """

        with self._lock:
            if self._initialized:
                return self._last_report

            self._publish("rollout_bootstrap_started", {})

            try:
                initialized_components = self.register_services()

            except DeploymentRolloutBootstrapError as exc:
                self._publish(
                    "rollout_bootstrap_failed",
                    {
                        "missing": list(exc.result.missing),
                        "cycles": [
                            list(cycle) for cycle in exc.result.cycles
                        ],
                    },
                )

                raise

            self.register_api()

            registered_jobs = self.register_scheduler_jobs()
            subscribed_events = self.register_event_handlers()

            now = self._clock()

            self._initialized = True
            self._started_at = now

            report = RolloutBootstrapReport(
                started=True,
                initialized_components=initialized_components,
                registered_jobs=registered_jobs,
                subscribed_events=subscribed_events,
                completed_at=now,
            )

            self._last_report = report

            self._publish("rollout_bootstrap_completed", report.to_dict())
            self._publish("rollout_runtime_ready", {})

            return report

    def health_check(self) -> "tuple[bool, str | None]":
        """
        Return (True, None) if initialized and every component wired
        at construction time is still present, else (False, reason).
        """

        with self._lock:
            initialized = self._initialized

        if not initialized:
            return False, "rollout bootstrap has not been initialized"

        missing = [
            name for name in _ROLLOUT_COMPONENT_ORDER
            if self._component(name) is None
        ]

        if missing:
            return False, (
                "components not wired: " + ", ".join(missing)
            )

        return True, None

    def shutdown(self) -> None:
        """
        Stop scheduler jobs, unsubscribe event handlers, ask the wired
        dashboard to refresh (see class docstring for why that is
        this bootstrap's "clear caches" step), and release this
        bootstrap's own state — a no-op if not currently initialized.
        """

        with self._lock:
            if not self._initialized:
                return

            job_ids = tuple(self._job_ids.values())
            self._job_ids = {}

            subscriptions = tuple(self._subscriptions)
            self._subscriptions = []

            if self._scheduler is not None:
                for job_id in job_ids:
                    try:
                        self._scheduler.unregister(job_id)

                    except KeyError:
                        pass

            if self._event_bus is not None:
                for subscription in subscriptions:
                    try:
                        self._event_bus.unsubscribe(subscription)

                    except ValueError:
                        pass

            if self._dashboard is not None:
                try:
                    self._dashboard.refresh()

                except Exception:
                    pass

            self._initialized = False
            self._started_at = None

            self._publish("rollout_runtime_shutdown", {})

    def restart(self) -> RolloutBootstrapReport:
        """
        Shut down (if currently initialized) and run the full
        initialization pipeline again.
        """

        self.shutdown()

        return self.initialize()

    def status(self) -> RolloutBootstrapStatus:
        """
        Return this bootstrap's current lifecycle state.
        """

        with self._lock:
            return RolloutBootstrapStatus(
                initialized=self._initialized,
                version=BOOTSTRAP_VERSION,
                started_at=self._started_at,
            )

    def last_event_at(self, event_type: str) -> "datetime | None":
        """
        Return when register_event_handlers()'s diagnostic handler
        last observed event_type, or None if it never has (or
        event_type is not in _TRACKED_EVENTS).
        """

        with self._lock:
            return self._last_event_at.get(event_type)

    def _publish(
        self, event_type: str, payload: "dict[str, Any] | None" = None
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="rollout-bootstrap", payload=payload
        )


def build_default_governance_rollout_bootstrap() -> (
    DeploymentRolloutBootstrap
):
    """
    Build the process-wide rollout bootstrap, wired to every
    process-wide rollout subsystem singleton from commits 1-12, the
    governance event bus, and the governance scheduler.

    Does not call initialize() — matching
    build_default_governance_scheduler_bootstrap, construction and
    initialization are deliberately separate: something else (the
    lifecycle manager's "rollout_manager" component, or a direct
    caller) triggers initialize() deliberately, the same way
    GovernanceSchedulerBootstrap's own singleton is never auto-
    initialized at import time either.
    """

    from .deployment_governance_blue_green import get_blue_green_engine
    from .deployment_governance_canary import get_canary_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_progressive_delivery import (
        get_progressive_delivery_engine,
    )
    from .deployment_governance_rollback import get_rollback_engine
    from .deployment_governance_rolling import get_rolling_engine
    from .deployment_governance_rollout_analytics import (
        get_rollout_analytics,
    )
    from .deployment_governance_rollout_dashboard import (
        get_rollout_dashboard,
    )
    from .deployment_governance_rollout_health import (
        get_rollout_health_engine,
    )
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_rollout_policy import (
        get_rollout_policy_engine,
    )
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_traffic_router import get_traffic_router
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return DeploymentRolloutBootstrap(
        event_bus=get_event_bus(),
        scheduler=get_scheduler(),
        version_registry=get_version_registry(),
        traffic_router=get_traffic_router(),
        rollout_manager=get_rollout_manager(),
        blue_green_engine=get_blue_green_engine(),
        canary_engine=get_canary_engine(),
        rolling_engine=get_rolling_engine(),
        progressive_engine=get_progressive_delivery_engine(),
        rollback_engine=get_rollback_engine(),
        health_engine=get_rollout_health_engine(),
        analytics=get_rollout_analytics(),
        policy_engine=get_rollout_policy_engine(),
        dashboard=get_rollout_dashboard(),
    )


# Shared for the lifetime of the process, matching
# _scheduler_bootstrap: whether the rollout subsystem has completed
# initialization needs to be visible to whatever queries it (the
# lifecycle manager's "rollout_manager" component, or a direct API
# caller).
_rollout_bootstrap = build_default_governance_rollout_bootstrap()


def get_rollout_bootstrap() -> DeploymentRolloutBootstrap:
    """
    Return the process-wide rollout bootstrap.
    """

    return _rollout_bootstrap
