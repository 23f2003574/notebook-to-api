from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from .deployment_governance_dependency_graph import GovernanceDependencyGraph

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus


@dataclass(frozen=True)
class LifecycleComponent:
    """
    A registered component's current lifecycle status.

    startup_priority is its position in the validated dependency
    graph's startup order (0 = starts first), or -1 if the graph is
    currently invalid (a missing or circular dependency) and no order
    could be computed.
    """

    name: str

    startup_priority: int

    started: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "startup_priority": self.startup_priority,
            "started": self.started,
        }


@dataclass(frozen=True)
class LifecycleReport:
    """
    The outcome of one lifecycle operation (startup/shutdown/restart/
    reload).

    started/stopped/failed are tuples rather than lists: a frozen
    dataclass only blocks reassigning its fields, not mutating a list
    stored in one, so tuples are what actually keep a report
    immutable once returned.
    """

    started: "tuple[str, ...]"

    stopped: "tuple[str, ...]"

    failed: "tuple[str, ...]"

    completed_at: datetime

    def __post_init__(self) -> None:
        if self.completed_at.tzinfo is None:
            raise ValueError(
                "completed_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "started": list(self.started),
            "stopped": list(self.stopped),
            "failed": list(self.failed),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass
class _RegisteredComponent:
    dependencies: "tuple[str, ...]"
    start: Callable[[], None]
    stop: Callable[[], None]
    reload: "Callable[[], None] | None"


class GovernanceLifecycleManager:
    """
    Coordinates startup, shutdown, restart, and reload of every
    registered governance component in one place, replacing the
    previous pattern of each caller sequencing
    component.start()/component.initialize()/component.shutdown()
    calls by hand in whatever order it assumed was correct.

    Startup order is not configured directly: it is computed from the
    dependency graph formed by each component's registered
    dependencies (see GovernanceDependencyGraph), the same graph
    introduced for validating configuration before runtime startup.
    Shutdown always proceeds in the exact reverse of that order, so a
    component is only torn down after everything depending on it has
    already stopped.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        self._components: "dict[str, _RegisteredComponent]" = {}

        self._started: "dict[str, bool]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register(
        self,
        name: str,
        *,
        dependencies: "tuple[str, ...]" = (),
        start: Callable[[], None],
        stop: Callable[[], None],
        reload: "Callable[[], None] | None" = None,
    ) -> None:
        """
        Register a component's start/stop/reload callables and the
        names of the components it depends on.

        Raises ValueError if name is already registered.
        """

        if name in self._components:
            raise ValueError(
                f"component '{name}' is already registered"
            )

        self._components[name] = _RegisteredComponent(
            dependencies=tuple(dependencies),
            start=start,
            stop=stop,
            reload=reload,
        )

        self._started[name] = False

    def startup(self) -> LifecycleReport:
        """
        Start every registered component that is not already started,
        in validated dependency order.

        Idempotent: a component already marked started is skipped
        rather than started again, so calling startup() repeatedly
        with nothing new registered is a no-op.

        If a component's start callable raises, startup stops
        attempting any further component (an unattempted component
        may depend on the one that just failed, so proceeding could
        build on a broken foundation) and reports it in failed;
        components started earlier in this call remain started.

        Raises GovernanceBootstrapError if the dependency graph itself
        is invalid (a missing or circular dependency) — that is
        aborted before any component is touched, not reported via
        failed.
        """

        from .deployment_governance_bootstrap import (
            GovernanceBootstrapError,
        )

        result = self._build_graph().validate()

        if not result.valid:
            raise GovernanceBootstrapError(result)

        started: "list[str]" = []
        failed: "list[str]" = []

        for name in result.startup_order:
            if self._started.get(name):
                continue

            try:
                self._components[name].start()

            except Exception:
                failed.append(name)
                self._publish("component_failed", name, {"phase": "startup"})
                break

            else:
                self._started[name] = True
                started.append(name)
                self._publish("component_started", name)

        report = LifecycleReport(
            started=tuple(started),
            stopped=(),
            failed=tuple(failed),
            completed_at=self._clock(),
        )

        self._publish_lifecycle_completed(report)

        return report

    def shutdown(self) -> LifecycleReport:
        """
        Stop every currently started component, in reverse startup
        order.

        Idempotent: a component that is not currently started is
        skipped rather than stopped again.

        Continues after individual failures: if a component's stop
        callable raises, it is recorded in failed and every remaining
        component is still attempted, so one misbehaving component
        can never prevent the rest of the runtime from shutting down.
        A component is marked not-started once its stop has been
        attempted, whether or not that attempt raised, since shutdown
        is best-effort and a component stuck reporting "started"
        forever after a failed stop would make restart() unusable.
        """

        try:
            order = self._build_graph().shutdown_order()

        except ValueError:
            # Graph currently invalid: still make a best-effort
            # attempt rather than refusing to stop anything.
            order = tuple(reversed(list(self._components)))

        stopped: "list[str]" = []
        failed: "list[str]" = []

        for name in order:
            if not self._started.get(name):
                continue

            try:
                self._components[name].stop()

            except Exception:
                failed.append(name)
                self._publish("component_failed", name, {"phase": "shutdown"})

            else:
                stopped.append(name)
                self._publish("component_stopped", name)

            finally:
                self._started[name] = False

        report = LifecycleReport(
            started=(),
            stopped=tuple(stopped),
            failed=tuple(failed),
            completed_at=self._clock(),
        )

        self._publish_lifecycle_completed(report)

        return report

    def restart(self) -> LifecycleReport:
        """
        Shut down every currently started component, then start every
        registered component back up, in validated dependency order.

        Composed from shutdown() followed by startup(): restart has
        no behavior of its own beyond running those two idempotent
        operations back to back, so bugs fixed in either are
        automatically reflected here too.
        """

        shutdown_report = self.shutdown()
        startup_report = self.startup()

        return LifecycleReport(
            started=startup_report.started,
            stopped=shutdown_report.stopped,
            failed=(
                shutdown_report.failed + startup_report.failed
            ),
            completed_at=startup_report.completed_at,
        )

    def reload(self) -> LifecycleReport:
        """
        Call reload() on every currently started component that
        registered one, in startup order (falling back to
        registration order if the graph is currently invalid, since
        reload only refreshes already-started components rather than
        constructing anything new).

        Components with no reload callable, or that are not currently
        started, are skipped. Continues after individual failures,
        like shutdown(): reload is meant to be a low-risk refresh, not
        a transition that should abort partway and leave the runtime
        in a worse state than before it was called.

        Reports successfully reloaded components via started (this
        report shape has no dedicated "reloaded" field), and leaves
        stopped empty.
        """

        result = self._build_graph().validate()

        order = (
            result.startup_order
            if result.valid
            else tuple(self._components)
        )

        reloaded: "list[str]" = []
        failed: "list[str]" = []

        for name in order:
            component = self._components[name]

            if (
                not self._started.get(name)
                or component.reload is None
            ):
                continue

            try:
                component.reload()

            except Exception:
                failed.append(name)
                self._publish("component_failed", name, {"phase": "reload"})

            else:
                reloaded.append(name)

        report = LifecycleReport(
            started=tuple(reloaded),
            stopped=(),
            failed=tuple(failed),
            completed_at=self._clock(),
        )

        self._publish_lifecycle_completed(report)

        return report

    def status(self) -> "tuple[LifecycleComponent, ...]":
        """
        Return every registered component's current lifecycle status,
        ordered by startup_priority then name for deterministic
        output.
        """

        result = self._build_graph().validate()

        priority = (
            {
                name: index
                for index, name in enumerate(result.startup_order)
            }
            if result.valid
            else {}
        )

        components = [
            LifecycleComponent(
                name=name,
                startup_priority=priority.get(name, -1),
                started=self._started.get(name, False),
            )
            for name in self._components
        ]

        return tuple(
            sorted(
                components,
                key=lambda component: (
                    component.startup_priority,
                    component.name,
                ),
            )
        )

    def _build_graph(self) -> GovernanceDependencyGraph:
        graph = GovernanceDependencyGraph()

        for name, component in self._components.items():
            graph.register(name, dependencies=component.dependencies)

        return graph

    def _publish(
        self,
        event_type: str,
        component_name: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=component_name, payload=payload
        )

    def _publish_lifecycle_completed(
        self, report: LifecycleReport
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            "lifecycle_completed",
            source="lifecycle_manager",
            payload=report.to_dict(),
        )


def build_default_governance_lifecycle_manager() -> (
    GovernanceLifecycleManager
):
    """
    Build the process-wide governance lifecycle manager's default
    component set, for a process with no live delivery runtime (this
    API server constructs no worker/scheduler anywhere).

    provider_registry, metrics_bootstrap, logging_bootstrap,
    delivery_runtime, health_service, readiness_service, and
    diagnostics_service are all registered with no-op start/stop:
    there is nothing real for a stateless process to start or stop on
    their behalf, and each is still registered so the dependency
    graph, startup order, and status() stay complete and honest about
    every component that conceptually exists.

    liveness_service is the one exception: it is wired to the real
    process-wide GovernanceLivenessService singleton, so
    starting/stopping this manager's liveness_service component
    genuinely starts/resets process liveness tracking.

    A process that does construct a real delivery runtime (a worker
    process, not this API server) should use
    deployment_governance_bootstrap.build_governance_lifecycle_manager
    instead, which wires delivery_runtime (and liveness_service) to
    that live runtime's own start()/stop().

    Wired to the process-wide governance event bus, so every
    component_started/component_stopped/component_failed/
    lifecycle_completed event described by this manager's operations
    is actually published, not just a documented possibility.
    """

    from .deployment_governance_bootstrap import (
        _COMPONENT_DEPENDENCIES,
    )
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_liveness import get_liveness_service

    manager = GovernanceLifecycleManager(event_bus=get_event_bus())

    def _noop() -> None:
        return None

    for name in (
        "provider_registry",
        "metrics_bootstrap",
        "logging_bootstrap",
        "delivery_runtime",
        "health_service",
        "readiness_service",
        "diagnostics_service",
    ):
        manager.register(
            name,
            dependencies=_COMPONENT_DEPENDENCIES[name],
            start=_noop,
            stop=_noop,
        )

    liveness_service = get_liveness_service()

    manager.register(
        "liveness_service",
        dependencies=_COMPONENT_DEPENDENCIES["liveness_service"],
        start=liveness_service.start,
        stop=liveness_service.reset,
    )

    return manager


# Shared for the lifetime of the process: lifecycle state (which
# components are currently started) is inherently process-wide, not
# something that can be meaningfully rebuilt fresh per request, the
# way the health/readiness/diagnostics services are.
_lifecycle_manager = build_default_governance_lifecycle_manager()


def get_lifecycle_manager() -> GovernanceLifecycleManager:
    """
    Return the process-wide governance lifecycle manager.

    Unlike get_liveness_service(), this does not implicitly start
    anything on access: startup/shutdown/restart are operations a
    caller triggers deliberately (e.g. via the
    POST /governance/lifecycle/* endpoints), not side effects of
    merely looking the manager up.
    """

    return _lifecycle_manager
