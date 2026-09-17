from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from .deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceDependencyGraph,
)

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_execution_manager import (
        GovernanceExecutionManager,
    )
    from .deployment_governance_job_registry import GovernanceJobRegistry


@dataclass(frozen=True)
class JobDependency:
    """
    A single job's registered prerequisites: the other jobs that must
    have already succeeded before this one is considered ready.
    """

    job_id: str

    depends_on: "tuple[str, ...]"

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if self.job_id in self.depends_on:
            raise ValueError(
                "job_id must not depend on itself"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class DependencyEvaluation:
    """
    The immutable outcome of evaluating one job's readiness at one
    point in time.
    """

    job_id: str

    ready: bool

    blocked_by: "tuple[str, ...]"

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if self.evaluated_at.tzinfo is None:
            raise ValueError(
                "evaluated_at must be timezone-aware"
            )

        if self.ready and self.blocked_by:
            raise ValueError(
                "blocked_by must be empty when ready is True"
            )

        if not self.ready and not self.blocked_by:
            raise ValueError(
                "blocked_by must not be empty when ready is False"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "ready": self.ready,
            "blocked_by": list(self.blocked_by),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class GovernanceJobDependencyManager:
    """
    Dependency-aware scheduling: jobs execute only after every job they
    depend on has already succeeded, extending the Scheduler's own
    tick (see GovernanceScheduler.run_due()'s optional
    dependency_manager parameter) without the Execution Manager itself
    ever needing to know dependencies exist — evaluate()/evaluate_all()
    only ever read execution_manager.history(), a pre-existing public
    accessor, and never call execute() or mutate the manager in any
    way.

    Circular- and missing-dependency detection is delegated to
    GovernanceDependencyGraph (the same graph the governance runtime's
    own component startup order uses) rather than reimplemented: a
    fresh graph is built from the current registry plus the proposed
    change on every register() call, so a registration that would
    introduce a cycle is rejected immediately — no broken state is
    ever stored, and execution_order() can therefore assume the
    currently registered set is always acyclic.

    A dependency job that has never registered its own JobDependency
    entry is not an error (it is simply a leaf with no prerequisites
    of its own) — "referenced jobs must exist" is checked against the
    job registry (a real job by that job_id), not against whether that
    job happens to have dependencies of its own registered here.

    Thread-safe: every mutation of the dependency registry is guarded
    by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
        execution_manager: "GovernanceExecutionManager | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._dependencies: "dict[str, tuple[str, ...]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._job_registry = job_registry

        self._execution_manager = execution_manager

    def register(
        self,
        job_id: str,
        *,
        depends_on: "tuple[str, ...]" = (),
    ) -> JobDependency:
        """
        Register job_id's prerequisites.

        Raises ValueError if job_id appears in its own depends_on, if
        job_id or any entry in depends_on is not a registered job
        (only checked when this manager was constructed with a
        job_registry), if job_id already has dependencies registered,
        or if registering would introduce a circular dependency
        (publishing "dependency_cycle_detected" first).
        """

        depends_on = tuple(depends_on)

        if job_id in depends_on:
            raise ValueError(
                f"job '{job_id}' cannot depend on itself"
            )

        if self._job_registry is not None:
            for reference in (job_id,) + depends_on:
                if not self._job_registry.exists(reference):
                    raise ValueError(
                        f"job '{reference}' is not registered"
                    )

        with self._lock:
            if job_id in self._dependencies:
                raise ValueError(
                    f"dependencies for job '{job_id}' are already "
                    "registered"
                )

            candidate = dict(self._dependencies)
            candidate[job_id] = depends_on

            cycles = self._detect_cycles(candidate)

            if not cycles:
                self._dependencies[job_id] = depends_on

        if cycles:
            self._publish(
                "dependency_cycle_detected",
                job_id,
                {"cycles": [list(cycle) for cycle in cycles]},
            )

            raise ValueError(
                f"registering job '{job_id}' would introduce a "
                f"circular dependency: {' -> '.join(cycles[0])}"
            )

        self._publish(
            "dependency_registered",
            job_id,
            {"depends_on": list(depends_on)},
        )

        return JobDependency(job_id=job_id, depends_on=depends_on)

    def remove(self, job_id: str) -> None:
        """
        Remove job_id's registered dependencies.

        Does not touch any other job's reference to job_id as a
        dependency: job_id simply becomes a leaf (no known
        prerequisites of its own) for whoever still depends on it,
        the same way GovernanceDependencyGraph.remove() leaves
        dangling references for its own validate() to describe.

        Raises KeyError if job_id has no registered dependencies.
        """

        with self._lock:
            if job_id not in self._dependencies:
                raise KeyError(
                    f"dependencies for job '{job_id}' are not "
                    "registered"
                )

            del self._dependencies[job_id]

        self._publish("dependency_removed", job_id, {})

    def dependencies(self, job_id: str) -> "tuple[str, ...]":
        """
        Return the job_ids job_id directly depends on.

        Raises KeyError if job_id has no registered dependencies.
        """

        with self._lock:
            if job_id not in self._dependencies:
                raise KeyError(
                    f"dependencies for job '{job_id}' are not "
                    "registered"
                )

            return self._dependencies[job_id]

    def dependents(self, job_id: str) -> "tuple[str, ...]":
        """
        Return every registered job_id that directly depends on
        job_id, ordered by job_id for deterministic output.

        Unlike dependencies(), this does not require job_id itself to
        have registered dependencies of its own.
        """

        with self._lock:
            return tuple(
                sorted(
                    candidate
                    for candidate, depends_on in self._dependencies.items()
                    if job_id in depends_on
                )
            )

    def evaluate(
        self, job_id: str, *, at: "datetime | None" = None
    ) -> DependencyEvaluation:
        """
        Evaluate whether job_id is ready to run at at (default now):
        every job in its depends_on must have its most recent
        execution (per the wired execution_manager's history())
        recorded as SUCCEEDED.

        A job with no registered dependencies (or no execution_manager
        wired at all, since readiness cannot be checked without one)
        is always ready. Publishes "dependency_blocked" or
        "dependency_resolved" accordingly — the latter only when
        job_id actually has dependencies that are now all satisfied,
        not for a dependency-less job trivially always being ready.
        """

        at = at or self._clock()

        with self._lock:
            depends_on = self._dependencies.get(job_id, ())

        blocked_by = tuple(
            dependency
            for dependency in depends_on
            if not self._has_succeeded(dependency)
        )

        ready = not blocked_by

        if blocked_by:
            self._publish(
                "dependency_blocked",
                job_id,
                {"blocked_by": list(blocked_by)},
            )

        elif ready and depends_on:
            self._publish("dependency_resolved", job_id, {})

        return DependencyEvaluation(
            job_id=job_id,
            ready=ready,
            blocked_by=blocked_by,
            evaluated_at=at,
        )

    def evaluate_all(
        self, *, at: "datetime | None" = None
    ) -> "tuple[DependencyEvaluation, ...]":
        """
        Evaluate every job with registered dependencies, ordered by
        job_id for deterministic output.
        """

        at = at or self._clock()

        with self._lock:
            job_ids = sorted(self._dependencies)

        return tuple(self.evaluate(job_id, at=at) for job_id in job_ids)

    def execution_order(self) -> "tuple[str, ...]":
        """
        Return the deterministic topological execution order across
        every job referenced by the currently registered dependencies
        (as a dependent or as a leaf prerequisite alike).

        Raises ValueError if the current registry is somehow not
        acyclic — register() prevents this from ever happening in the
        first place, so this only guards against it, it should never
        actually trigger in practice.
        """

        with self._lock:
            dependencies_map = dict(self._dependencies)

        result = self._build_graph(dependencies_map).validate()

        if not result.valid:
            raise ValueError(
                "job dependency graph is not valid; call validate() "
                "for details"
            )

        return result.startup_order

    def validate(self) -> DependencyValidationResult:
        """
        Validate the current dependency registry: missing references
        and circular dependencies, plus the resulting deterministic
        topological order if valid.
        """

        with self._lock:
            dependencies_map = dict(self._dependencies)

        return self._build_graph(dependencies_map).validate()

    def clear(self) -> None:
        """
        Remove every registered job dependency.
        """

        with self._lock:
            self._dependencies.clear()

    def _has_succeeded(self, job_id: str) -> bool:
        if self._execution_manager is None:
            return True

        results = self._execution_manager.history(job_id, limit=1)

        return bool(results) and results[0].status == "SUCCEEDED"

    def _build_graph(
        self, dependencies_map: "dict[str, tuple[str, ...]]"
    ) -> GovernanceDependencyGraph:
        graph = GovernanceDependencyGraph()

        all_job_ids = set(dependencies_map)

        for depends_on in dependencies_map.values():
            all_job_ids.update(depends_on)

        for job_id in all_job_ids:
            graph.register(
                job_id, dependencies=dependencies_map.get(job_id, ())
            )

        return graph

    def _detect_cycles(
        self, dependencies_map: "dict[str, tuple[str, ...]]"
    ) -> "tuple[tuple[str, ...], ...]":
        return self._build_graph(dependencies_map).validate().cycles

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


def build_default_governance_job_dependency_manager() -> (
    GovernanceJobDependencyManager
):
    """
    Build the process-wide governance job dependency manager, wired to
    the process-wide governance event bus, job registry, and execution
    manager.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from .deployment_governance_job_registry import get_job_registry

    return GovernanceJobDependencyManager(
        event_bus=get_event_bus(),
        job_registry=get_job_registry(),
        execution_manager=get_execution_manager(),
    )


# Shared for the lifetime of the process: dependencies registered
# through the API need to be visible to whatever queries the manager
# directly (the scheduler's own tick, or a direct API caller), which a
# persistence runtime built fresh per request cannot provide on its
# own.
_job_dependency_manager = build_default_governance_job_dependency_manager()


def get_job_dependency_manager() -> GovernanceJobDependencyManager:
    """
    Return the process-wide governance job dependency manager.
    """

    return _job_dependency_manager
