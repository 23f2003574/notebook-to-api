from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_version_registry import is_semantic_version

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )

# The default traffic-percentage progression a canary advances
# through, one promote() call at a time. Configurable per deploy()
# call (or per engine, via the constructor's default_stages) —
# nothing below depends on this exact sequence, only on the shape
# validated by _validate_stages: starts at 0, ends at 100, strictly
# increasing.
DEFAULT_STAGES: "tuple[int, ...]" = (0, 5, 10, 25, 50, 75, 100)


def _validate_stages(stages: "tuple[int, ...]") -> None:
    if not stages:
        raise ValueError("stages must not be empty")

    if stages[0] != 0:
        raise ValueError("stages must start at 0")

    if stages[-1] != 100:
        raise ValueError("stages must end at 100")

    for previous, current in zip(stages, stages[1:]):
        if current <= previous:
            raise ValueError(
                "stages must be strictly increasing"
            )

        if not 0 <= current <= 100:
            raise ValueError(
                "every stage must be between 0 and 100"
            )


@dataclass(frozen=True)
class CanaryDeployment:
    """
    One deployment's current canary rollout state: which versions are
    involved, how much traffic the canary currently receives, and
    which stage of its (deployment-specific) progression it is at.
    """

    deployment_id: str

    stable_version: str

    canary_version: str

    traffic_percentage: int

    stage: int

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not is_semantic_version(self.stable_version):
            raise ValueError(
                f"stable_version '{self.stable_version}' is not a "
                "valid semantic version"
            )

        if not is_semantic_version(self.canary_version):
            raise ValueError(
                f"canary_version '{self.canary_version}' is not a "
                "valid semantic version"
            )

        if not 0 <= self.traffic_percentage <= 100:
            raise ValueError(
                "traffic_percentage must be between 0 and 100"
            )

        if self.stage < 0:
            raise ValueError("stage must be >= 0")

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "stable_version": self.stable_version,
            "canary_version": self.canary_version,
            "traffic_percentage": self.traffic_percentage,
            "stage": self.stage,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class CanaryEvaluation:
    """
    One immutable health-evaluation outcome, recorded at whatever
    traffic_percentage the canary was receiving at evaluation time.
    """

    deployment_id: str

    healthy: bool

    traffic_percentage: int

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not 0 <= self.traffic_percentage <= 100:
            raise ValueError(
                "traffic_percentage must be between 0 and 100"
            )

        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "healthy": self.healthy,
            "traffic_percentage": self.traffic_percentage,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class CanaryDeploymentEngine:
    """
    Gradually shifts traffic from a deployment's stable version to a
    new canary version across a configurable, strictly increasing
    stage progression (DEFAULT_STAGES unless overridden), gated at
    every step by a health evaluation. The Rollout Manager
    (deployment_governance_rollout_manager) delegates strategy=
    "CANARY" rollout completion to this engine — see
    DeploymentRolloutManager.complete().

    stable_version, when not given explicitly to deploy(), is
    resolved through the Version Registry (deployment_governance_
    version_registry).

    If a scheduler (deployment_governance_scheduler) is wired in,
    deploy() registers a recurring job there for the deployment's
    periodic health evaluations, unregistered again once the canary
    reaches a terminal state (COMPLETED via promote(), or rolled
    back). That job is purely declarative, matching how every other
    scheduled job in this codebase works absent a live delivery
    runtime — something else (a caller, or a future worker process)
    is still responsible for actually invoking evaluate() when it is
    due.

    If a metrics service (deployment_governance_scheduler_metrics) is
    wired in, every evaluate() call's outcome and duration are
    recorded into it, folding canary health checks into the same
    scheduler performance metrics every other governance job
    contributes to.

    Thread-safe: every mutation of engine state is guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        default_stages: "tuple[int, ...] | None" = None,
        evaluation_interval_seconds: int = 60,
    ) -> None:
        default_stages = default_stages or DEFAULT_STAGES

        _validate_stages(default_stages)

        self._lock = threading.Lock()

        self._deployments: "dict[str, CanaryDeployment]" = {}

        self._stages: "dict[str, tuple[int, ...]]" = {}

        self._active_deployment_ids: "set[str]" = set()

        self._paused: "dict[str, bool]" = {}

        self._evaluated: "dict[str, bool]" = {}

        self._history: "dict[str, list[CanaryEvaluation]]" = {}

        self._scheduler_jobs: "dict[str, str]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._version_registry = version_registry

        self._scheduler = scheduler

        self._metrics = metrics

        self._traffic_router = traffic_router

        self._default_stages = default_stages

        self._evaluation_interval_seconds = evaluation_interval_seconds

    def deploy(
        self,
        deployment_id: str,
        canary_version: str,
        stable_version: "str | None" = None,
        stages: "tuple[int, ...] | None" = None,
    ) -> CanaryDeployment:
        """
        Start a new canary rollout for deployment_id at its first
        configured stage (0% traffic to canary_version).

        Raises ValueError if deployment_id already has an active
        (non-terminal) canary, if canary_version is not a valid
        semantic version, if stages is given but malformed (see
        _validate_stages), or if stable_version is omitted and no
        version_registry is wired.
        """

        resolved_stages = stages or self._default_stages

        _validate_stages(resolved_stages)

        with self._lock:
            if deployment_id in self._active_deployment_ids:
                raise ValueError(
                    f"deployment '{deployment_id}' already has an "
                    "active canary"
                )

            if stable_version is not None:
                resolved_stable_version = stable_version
            elif self._version_registry is not None:
                resolved_stable_version = self._version_registry.get(
                    deployment_id
                ).version
            else:
                raise ValueError(
                    "stable_version must be provided when no "
                    "version_registry is wired"
                )

            now = self._clock()

            record = CanaryDeployment(
                deployment_id=deployment_id,
                stable_version=resolved_stable_version,
                canary_version=canary_version,
                traffic_percentage=resolved_stages[0],
                stage=0,
                created_at=now,
            )

            self._deployments[deployment_id] = record
            self._stages[deployment_id] = resolved_stages
            self._active_deployment_ids.add(deployment_id)
            self._paused[deployment_id] = False
            self._evaluated[deployment_id] = False
            self._history.setdefault(deployment_id, [])

        if self._scheduler is not None:
            job = self._scheduler.register(
                f"canary-evaluation-{deployment_id}",
                interval_seconds=self._evaluation_interval_seconds,
                namespace="canary",
                description=(
                    "Periodic health evaluation for canary "
                    f"deployment '{deployment_id}'"
                ),
            )

            with self._lock:
                self._scheduler_jobs[deployment_id] = job.job_id

        self._publish(
            "canary_started",
            deployment_id,
            {
                "stable_version": resolved_stable_version,
                "canary_version": canary_version,
            },
        )

        self._route_configure(
            deployment_id,
            [
                (resolved_stable_version, float(100 - resolved_stages[0])),
                (canary_version, float(resolved_stages[0])),
            ],
        )

        return record

    def evaluate(
        self,
        deployment_id: str,
        check: "Callable[[], bool] | None" = None,
    ) -> CanaryEvaluation:
        """
        Run one health evaluation for deployment_id's canary at its
        current traffic percentage.

        check, if given, is called with no arguments and its return
        value determines success; omitted, evaluation always
        succeeds. A failing evaluation automatically rolls the canary
        back (see rollback()) — this engine never leaves an unhealthy
        canary receiving traffic.

        Raises KeyError if deployment_id has no canary record, or
        ValueError if its canary is not currently active (already
        completed or rolled back).
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no canary "
                    "record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"canary '{deployment_id}' is not active"
                )

            traffic_percentage = record.traffic_percentage

        started = time.monotonic()

        healthy = True if check is None else bool(check())

        execution_ms = (time.monotonic() - started) * 1000

        now = self._clock()

        evaluation = CanaryEvaluation(
            deployment_id=deployment_id,
            healthy=healthy,
            traffic_percentage=traffic_percentage,
            evaluated_at=now,
        )

        with self._lock:
            self._history[deployment_id].append(evaluation)

            if healthy:
                self._evaluated[deployment_id] = True

        if self._metrics is not None:
            if healthy:
                self._metrics.record_completion(
                    execution_ms=execution_ms
                )
            else:
                self._metrics.record_failure(
                    execution_ms=execution_ms
                )

        if not healthy:
            self._publish("canary_failed", deployment_id, {})
            self.rollback(deployment_id)

        return evaluation

    def promote(self, deployment_id: str) -> CanaryDeployment:
        """
        Advance deployment_id's canary to the next configured stage.

        Raises KeyError if deployment_id has no canary record,
        ValueError if its canary is not active, is paused, or has not
        had a successful evaluate() call since its last promote()/
        deploy().

        Reaching the final configured stage (100%) marks the canary
        COMPLETED: it is removed from the active set (freeing
        deployment_id for a future deploy()) and its scheduled
        evaluation job, if any, is unregistered.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no canary "
                    "record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"canary '{deployment_id}' is not active"
                )

            if self._paused.get(deployment_id, False):
                raise ValueError(
                    f"canary '{deployment_id}' is paused"
                )

            if not self._evaluated.get(deployment_id, False):
                raise ValueError(
                    f"canary '{deployment_id}' has not passed a "
                    "health evaluation since its last promotion"
                )

            stages = self._stages[deployment_id]
            next_stage = record.stage + 1

            if next_stage >= len(stages):
                raise ValueError(
                    f"canary '{deployment_id}' is already at its "
                    "final stage"
                )

            updated = replace(
                record,
                stage=next_stage,
                traffic_percentage=stages[next_stage],
            )

            self._deployments[deployment_id] = updated
            self._evaluated[deployment_id] = False

            completed = next_stage == len(stages) - 1

            if completed:
                self._active_deployment_ids.discard(deployment_id)

        self._publish(
            "canary_promoted",
            deployment_id,
            {"traffic_percentage": updated.traffic_percentage},
        )

        self._route_allocate(
            deployment_id, updated.canary_version,
            float(updated.traffic_percentage),
        )

        if completed:
            self._unregister_scheduler_job(deployment_id)

            self._publish("canary_completed", deployment_id, {})

        return updated

    def pause(self, deployment_id: str) -> CanaryDeployment:
        """
        Pause deployment_id's canary, blocking promote() until
        resume() is called.

        Idempotent: a no-op (still returning the current record) if
        already paused. Raises KeyError if deployment_id has no
        canary record, or ValueError if its canary is not active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no canary "
                    "record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"canary '{deployment_id}' is not active"
                )

            already_paused = self._paused.get(deployment_id, False)

            self._paused[deployment_id] = True

        if not already_paused:
            self._publish("canary_paused", deployment_id, {})

        return record

    def resume(self, deployment_id: str) -> CanaryDeployment:
        """
        Resume deployment_id's paused canary.

        Idempotent: a no-op (still returning the current record) if
        not currently paused. Raises KeyError if deployment_id has no
        canary record, or ValueError if its canary is not active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no canary "
                    "record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"canary '{deployment_id}' is not active"
                )

            was_paused = self._paused.get(deployment_id, False)

            self._paused[deployment_id] = False

        if was_paused:
            self._publish("canary_resumed", deployment_id, {})

        return record

    def rollback(self, deployment_id: str) -> CanaryDeployment:
        """
        Roll deployment_id's canary back to 0% traffic and mark it
        terminal, freeing deployment_id for a future deploy().

        Raises KeyError if deployment_id has no canary record, or
        ValueError if its canary is not currently active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no canary "
                    "record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"canary '{deployment_id}' is not active"
                )

            stages = self._stages[deployment_id]

            updated = replace(
                record, stage=0, traffic_percentage=stages[0],
            )

            self._deployments[deployment_id] = updated
            self._active_deployment_ids.discard(deployment_id)
            self._evaluated[deployment_id] = False

        self._unregister_scheduler_job(deployment_id)

        self._publish("canary_rolled_back", deployment_id, {})

        self._route_allocate(deployment_id, updated.canary_version, 0.0)

        return updated

    def status(self, deployment_id: str) -> CanaryDeployment:
        """
        Return deployment_id's current canary state.

        Raises KeyError if deployment_id has no canary record.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no canary "
                    "record"
                )

            return record

    def history(
        self, deployment_id: str
    ) -> "tuple[CanaryEvaluation, ...]":
        """
        Return every health evaluation ever recorded for
        deployment_id, oldest first. Returns an empty tuple if
        deployment_id has never been deployed.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def list(self) -> "tuple[CanaryDeployment, ...]":
        """
        Return every currently tracked canary deployment, ordered
        deterministically by deployment_id.
        """

        with self._lock:
            records = list(self._deployments.values())

        return tuple(
            sorted(records, key=lambda record: record.deployment_id)
        )

    def clear(self) -> None:
        """
        Remove every tracked canary deployment, its progression
        state, and its history. Does not unregister any scheduler
        jobs — callers resetting a shared scheduler should clear it
        separately.
        """

        with self._lock:
            self._deployments.clear()
            self._stages.clear()
            self._active_deployment_ids.clear()
            self._paused.clear()
            self._evaluated.clear()
            self._history.clear()
            self._scheduler_jobs.clear()

    def _unregister_scheduler_job(self, deployment_id: str) -> None:
        if self._scheduler is None:
            return

        with self._lock:
            job_id = self._scheduler_jobs.pop(deployment_id, None)

        if job_id is not None:
            try:
                self._scheduler.unregister(job_id)

            except KeyError:
                pass

    def _route_configure(
        self,
        deployment_id: str,
        allocations: "list[tuple[str, float]]",
    ) -> None:
        if self._traffic_router is None:
            return

        try:
            self._traffic_router.configure(
                deployment_id, allocations, strategy="CANARY"
            )

        except ValueError:
            pass

    def _route_allocate(
        self, deployment_id: str, version: str, percentage: float
    ) -> None:
        if self._traffic_router is None:
            return

        try:
            self._traffic_router.allocate(
                deployment_id, version, percentage
            )

        except (KeyError, ValueError):
            pass

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


def build_default_governance_canary_engine() -> CanaryDeploymentEngine:
    """
    Build the process-wide canary deployment engine, wired to the
    process-wide governance event bus, deployment version registry,
    scheduler, scheduler metrics, and traffic router.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from .deployment_governance_traffic_router import get_traffic_router
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return CanaryDeploymentEngine(
        event_bus=get_event_bus(),
        version_registry=get_version_registry(),
        scheduler=get_scheduler(),
        metrics=get_scheduler_metrics(),
        traffic_router=get_traffic_router(),
    )


# Shared for the lifetime of the process: which deployments have an
# active canary, and their progression/history, is inherently
# process-wide, not something that can be meaningfully rebuilt fresh
# per request.
_canary_engine = build_default_governance_canary_engine()


def get_canary_engine() -> CanaryDeploymentEngine:
    """
    Return the process-wide canary deployment engine.
    """

    return _canary_engine
