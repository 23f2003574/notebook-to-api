from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Sequence, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_blue_green import (
        BlueGreenDeploymentEngine,
    )
    from .deployment_governance_canary import CanaryDeploymentEngine
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rolling import RollingDeploymentEngine
    from .deployment_governance_rollout_health import (
        DeploymentRolloutHealthEngine,
    )
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )

# What a pipeline stage's work actually is. CANARY/ROLLING/BLUE_GREEN
# delegate one incremental step to the matching engine (if wired in);
# MANUAL_APPROVAL and HEALTH_VALIDATION have no engine of their own —
# for MANUAL_APPROVAL the approval gate itself is the entire stage,
# and for HEALTH_VALIDATION the check passed to advance() is.
PROGRESSIVE_STAGE_STRATEGIES: "tuple[str, ...]" = (
    "CANARY",
    "ROLLING",
    "BLUE_GREEN",
    "MANUAL_APPROVAL",
    "HEALTH_VALIDATION",
)

PROGRESSIVE_DEPLOYMENT_STATES: "tuple[str, ...]" = (
    "RUNNING",
    "AWAITING_APPROVAL",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "ROLLED_BACK",
)

_TERMINAL_STATES: "frozenset[str]" = frozenset(
    {"COMPLETED", "FAILED", "ROLLED_BACK"}
)


@dataclass(frozen=True)
class ProgressiveStage:
    """
    One stage of a progressive delivery pipeline: what kind of work it
    does, whether it needs a human to sign off before advancing past
    it, and whether it has been completed yet.
    """

    stage_id: str

    name: str

    strategy: str

    approval_required: bool

    completed: bool

    def __post_init__(self) -> None:
        if not self.stage_id:
            raise ValueError("stage_id must not be empty")

        if not self.name:
            raise ValueError("name must not be empty")

        if self.strategy not in PROGRESSIVE_STAGE_STRATEGIES:
            raise ValueError(
                f"strategy must be one of {PROGRESSIVE_STAGE_STRATEGIES}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "name": self.name,
            "strategy": self.strategy,
            "approval_required": self.approval_required,
            "completed": self.completed,
        }


@dataclass(frozen=True)
class ProgressiveDeployment:
    """
    One deployment's current position in its progressive delivery
    pipeline.
    """

    deployment_id: str

    current_stage: int

    total_stages: int

    state: str

    started_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.total_stages <= 0:
            raise ValueError("total_stages must be greater than 0")

        if not 0 <= self.current_stage <= self.total_stages:
            raise ValueError(
                "current_stage must be between 0 and total_stages"
            )

        if self.state not in PROGRESSIVE_DEPLOYMENT_STATES:
            raise ValueError(
                f"state must be one of {PROGRESSIVE_DEPLOYMENT_STATES}"
            )

        if self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "current_stage": self.current_stage,
            "total_stages": self.total_stages,
            "state": self.state,
            "started_at": self.started_at.isoformat(),
        }


class ProgressiveDeliveryEngine:
    """
    Automates a deployment through a configurable, ordered pipeline of
    stages — each backed by Canary, Rolling, Blue/Green, a manual
    approval gate, or a plain health validation — advancing one stage
    at a time via advance(), gated by approve()/reject() wherever a
    stage requires sign-off.

    Unlike CanaryDeploymentEngine (which tracks traffic percentage
    directly), this engine coordinates the existing per-strategy
    engines rather than performing any traffic/instance mechanics
    itself: for a CANARY/ROLLING/BLUE_GREEN stage, advancing past it
    delegates exactly one incremental step to the matching engine (if
    wired in) — CanaryDeploymentEngine.promote,
    RollingDeploymentEngine.next_batch, or
    BlueGreenDeploymentEngine.switch — the same one-step delegation
    DeploymentRolloutManager.complete() itself uses. If the relevant
    engine has nothing staged for this deployment_id, that delegation
    is silently skipped; this engine's own stage bookkeeping does not
    depend on it succeeding.

    A failing advance() (its check callable returning False)
    automatically rolls the whole deployment back, unlike
    RollingDeploymentEngine's pause-on-failure — there is no
    "revalidate the same stage" workflow here, since stages compose
    heterogeneous underlying strategies.

    If a scheduler is wired in, deploy() registers a recurring job
    there for the deployment's stage-advancement interval,
    unregistered again once the deployment reaches a terminal state —
    the same declarative pattern CanaryDeploymentEngine and
    RollingDeploymentEngine use.

    Unlike the three per-strategy engines, this one has no version or
    percentage data of its own to hand the DeploymentTrafficRouter —
    whatever routing exists for a given stage was already configured
    by whichever sub-engine that stage delegated to. The one thing
    this engine can meaningfully do with a wired-in traffic_router is
    clean up: rollback() resets deployment_id's routing table, best
    effort, since the whole pipeline (and whatever traffic shift its
    stages produced) is being abandoned.

    Thread-safe: every mutation of engine state is guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        canary_engine: "CanaryDeploymentEngine | None" = None,
        rolling_engine: "RollingDeploymentEngine | None" = None,
        blue_green_engine: "BlueGreenDeploymentEngine | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        health_engine: "DeploymentRolloutHealthEngine | None" = None,
        stage_interval_seconds: int = 60,
    ) -> None:
        self._lock = threading.Lock()

        self._deployments: "dict[str, ProgressiveDeployment]" = {}

        self._pipelines: "dict[str, tuple[ProgressiveStage, ...]]" = {}

        self._active_deployment_ids: "set[str]" = set()

        self._paused: "dict[str, bool]" = {}

        self._approved: "dict[str, bool]" = {}

        self._history: "dict[str, list[ProgressiveStage]]" = {}

        self._scheduler_jobs: "dict[str, str]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._canary_engine = canary_engine

        self._rolling_engine = rolling_engine

        self._blue_green_engine = blue_green_engine

        self._scheduler = scheduler

        self._traffic_router = traffic_router

        self._health_engine = health_engine

        self._stage_interval_seconds = stage_interval_seconds

    def deploy(
        self,
        deployment_id: str,
        stages: "Sequence[tuple[str, str, bool]]",
    ) -> ProgressiveDeployment:
        """
        Start a new progressive delivery pipeline for deployment_id.

        stages is an ordered sequence of (name, strategy,
        approval_required) tuples — strategy must be one of
        PROGRESSIVE_STAGE_STRATEGIES.

        Raises ValueError if deployment_id already has an active
        progressive deployment, if stages is empty, or if any stage's
        strategy is invalid.
        """

        if not stages:
            raise ValueError("stages must not be empty")

        for _, strategy, _ in stages:
            if strategy not in PROGRESSIVE_STAGE_STRATEGIES:
                raise ValueError(
                    "strategy must be one of "
                    f"{PROGRESSIVE_STAGE_STRATEGIES}"
                )

        with self._lock:
            if deployment_id in self._active_deployment_ids:
                raise ValueError(
                    f"deployment '{deployment_id}' already has an "
                    "active progressive deployment"
                )

            now = self._clock()

            built_stages = tuple(
                ProgressiveStage(
                    stage_id=str(uuid4()),
                    name=name,
                    strategy=strategy,
                    approval_required=approval_required,
                    completed=False,
                )
                for name, strategy, approval_required in stages
            )

            record = ProgressiveDeployment(
                deployment_id=deployment_id,
                current_stage=0,
                total_stages=len(built_stages),
                state="RUNNING",
                started_at=now,
            )

            self._deployments[deployment_id] = record
            self._pipelines[deployment_id] = built_stages
            self._active_deployment_ids.add(deployment_id)
            self._paused[deployment_id] = False
            self._approved[deployment_id] = False
            self._history.setdefault(deployment_id, [])

        if self._scheduler is not None:
            job = self._scheduler.register(
                f"progressive-stage-{deployment_id}",
                interval_seconds=self._stage_interval_seconds,
                namespace="progressive",
                description=(
                    "Stage-advancement interval for progressive "
                    f"delivery deployment '{deployment_id}'"
                ),
            )

            with self._lock:
                self._scheduler_jobs[deployment_id] = job.job_id

        self._publish(
            "progressive_started",
            deployment_id,
            {"total_stages": len(built_stages)},
        )

        self._publish(
            "stage_started",
            deployment_id,
            {"stage": built_stages[0].to_dict()},
        )

        return record

    def advance(
        self,
        deployment_id: str,
        check: "Callable[[], bool] | None" = None,
    ) -> ProgressiveDeployment:
        """
        Attempt to complete the current stage and move on to the
        next.

        If the current stage requires approval and has not yet been
        approved, this transitions the deployment to
        AWAITING_APPROVAL (publishing approval_requested, once) and
        raises ValueError — call approve() first. Otherwise, check (if
        given) determines the stage's health; omitted, and with a
        health_engine wired in, health instead comes from consulting
        it (see CanaryDeploymentEngine.evaluate() for the exact
        rule); omitted with no health_engine wired either, health
        always passes. A failure automatically rolls the whole
        deployment back. On success, the current
        stage's matching engine (if any) is delegated one incremental
        step, the stage is marked completed in history, and the
        pipeline either enters its next stage or, if that was the
        final stage, COMPLETED.

        Raises KeyError if deployment_id has no progressive deployment
        record, or ValueError if it is not active, is paused, or its
        current stage is awaiting approval.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "not active"
                )

            if self._paused.get(deployment_id, False):
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "paused"
                )

            stage = self._pipelines[deployment_id][record.current_stage]

            if stage.approval_required and not self._approved.get(
                deployment_id, False
            ):
                first_request = record.state != "AWAITING_APPROVAL"

                if first_request:
                    self._deployments[deployment_id] = replace(
                        record, state="AWAITING_APPROVAL"
                    )

            else:
                first_request = False

        if stage.approval_required and not self._approved.get(
            deployment_id, False
        ):
            if first_request:
                self._publish(
                    "approval_requested",
                    deployment_id,
                    {"stage": stage.to_dict()},
                )

            raise ValueError(
                f"stage '{stage.name}' requires approval before "
                "advancing"
            )

        if check is not None:
            healthy = bool(check())

        elif self._health_engine is not None:
            health_snapshot = self._health_engine.evaluate(
                deployment_id
            )

            healthy = (
                self._health_engine.decision_for(
                    health_snapshot.status
                )
                == "CONTINUE"
            )

        else:
            healthy = True

        if not healthy:
            self._publish("progressive_failed", deployment_id, {
                "stage": stage.to_dict(),
            })

            return self.rollback(deployment_id)

        self._delegate_to_strategy_engine(stage.strategy, deployment_id)

        completed_stage = replace(stage, completed=True)

        with self._lock:
            self._history[deployment_id].append(completed_stage)

            next_stage_index = record.current_stage + 1
            completed = next_stage_index >= record.total_stages

            if completed:
                updated = replace(
                    record, current_stage=next_stage_index,
                    state="COMPLETED",
                )
                self._active_deployment_ids.discard(deployment_id)

            else:
                updated = replace(
                    record, current_stage=next_stage_index,
                    state="RUNNING",
                )
                self._approved[deployment_id] = False

            self._deployments[deployment_id] = updated

        self._publish(
            "stage_completed", deployment_id,
            {"stage": completed_stage.to_dict()},
        )

        if completed:
            self._unregister_scheduler_job(deployment_id)

            self._publish("progressive_completed", deployment_id, {})

        else:
            next_stage = self._pipelines[deployment_id][
                next_stage_index
            ]

            self._publish(
                "stage_started", deployment_id,
                {"stage": next_stage.to_dict()},
            )

        return updated

    def approve(self, deployment_id: str) -> ProgressiveDeployment:
        """
        Grant the pending approval for deployment_id's current stage,
        unblocking the next advance() call.

        Raises KeyError if deployment_id has no progressive deployment
        record, or ValueError if it is not active or has no approval
        currently pending.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "not active"
                )

            if record.state != "AWAITING_APPROVAL":
                raise ValueError(
                    f"progressive deployment '{deployment_id}' has "
                    "no approval currently pending"
                )

            self._approved[deployment_id] = True

            updated = replace(record, state="RUNNING")

            self._deployments[deployment_id] = updated

        self._publish("approval_granted", deployment_id, {})

        return updated

    def reject(self, deployment_id: str) -> ProgressiveDeployment:
        """
        Reject deployment_id's pending approval, publishing
        approval_rejected and automatically rolling the whole
        deployment back.

        Raises KeyError if deployment_id has no progressive deployment
        record, or ValueError if it is not active or has no approval
        currently pending.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "not active"
                )

            if record.state != "AWAITING_APPROVAL":
                raise ValueError(
                    f"progressive deployment '{deployment_id}' has "
                    "no approval currently pending"
                )

        self._publish("approval_rejected", deployment_id, {})

        return self.rollback(deployment_id)

    def pause(self, deployment_id: str) -> ProgressiveDeployment:
        """
        Pause deployment_id's pipeline, blocking advance() until
        resume() is called.

        Idempotent: a no-op (still returning the current record) if
        already paused. Raises KeyError if deployment_id has no
        progressive deployment record, or ValueError if it is not
        active. Unlike the per-strategy engines' pause()/resume(),
        this publishes no event of its own — the documented
        progressive delivery event vocabulary has no pause/resume
        entries, only the stage- and approval-level events above.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "not active"
                )

            self._paused[deployment_id] = True

        return record

    def resume(self, deployment_id: str) -> ProgressiveDeployment:
        """
        Resume deployment_id's paused pipeline.

        Idempotent: a no-op (still returning the current record) if
        not currently paused. Raises KeyError if deployment_id has no
        progressive deployment record, or ValueError if it is not
        active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "not active"
                )

            self._paused[deployment_id] = False

        return record

    def rollback(self, deployment_id: str) -> ProgressiveDeployment:
        """
        Mark deployment_id's progressive deployment ROLLED_BACK at
        whatever stage it is currently on, freeing deployment_id for
        a future deploy().

        Raises KeyError if deployment_id has no progressive deployment
        record, or ValueError if it is not currently active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"progressive deployment '{deployment_id}' is "
                    "not active"
                )

            updated = replace(record, state="ROLLED_BACK")

            self._deployments[deployment_id] = updated
            self._active_deployment_ids.discard(deployment_id)

        self._unregister_scheduler_job(deployment_id)

        self._publish("progressive_rolled_back", deployment_id, {})

        if self._traffic_router is not None:
            try:
                self._traffic_router.reset(deployment_id)

            except (KeyError, ValueError):
                pass

        return updated

    def status(self, deployment_id: str) -> ProgressiveDeployment:
        """
        Return deployment_id's current progressive deployment state.

        Raises KeyError if deployment_id has no progressive deployment
        record.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            return record

    def history(
        self, deployment_id: str
    ) -> "tuple[ProgressiveStage, ...]":
        """
        Return every stage completed so far for deployment_id, in
        pipeline order. Returns an empty tuple if deployment_id has
        never been deployed or has not completed any stage yet.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def pipeline(
        self, deployment_id: str
    ) -> "tuple[ProgressiveStage, ...]":
        """
        Return deployment_id's full configured pipeline, in order,
        regardless of completion state.

        Raises KeyError if deployment_id has no progressive deployment
        record.
        """

        with self._lock:
            stages = self._pipelines.get(deployment_id)

            if stages is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no progressive "
                    "deployment record"
                )

            return stages

    def list(self) -> "tuple[ProgressiveDeployment, ...]":
        """
        Return every currently tracked progressive deployment, ordered
        deterministically by deployment_id.
        """

        with self._lock:
            records = list(self._deployments.values())

        return tuple(
            sorted(records, key=lambda record: record.deployment_id)
        )

    def clear(self) -> None:
        """
        Remove every tracked progressive deployment, its pipeline, and
        its history. Does not unregister any scheduler jobs — callers
        resetting a shared scheduler should clear it separately.
        """

        with self._lock:
            self._deployments.clear()
            self._pipelines.clear()
            self._active_deployment_ids.clear()
            self._paused.clear()
            self._approved.clear()
            self._history.clear()
            self._scheduler_jobs.clear()

    def set_health_engine(
        self, health_engine: "DeploymentRolloutHealthEngine"
    ) -> None:
        """
        Wire health_engine in after construction — see
        CanaryDeploymentEngine.set_health_engine for why this exists
        instead of a constructor-injected singleton.
        """

        self._health_engine = health_engine

    def _delegate_to_strategy_engine(
        self, strategy: str, deployment_id: str
    ) -> None:
        if strategy == "CANARY" and self._canary_engine is not None:
            try:
                self._canary_engine.promote(deployment_id)

            except (KeyError, ValueError):
                pass

        elif strategy == "ROLLING" and self._rolling_engine is not None:
            try:
                self._rolling_engine.next_batch(deployment_id)

            except (KeyError, ValueError):
                pass

        elif (
            strategy == "BLUE_GREEN"
            and self._blue_green_engine is not None
        ):
            try:
                self._blue_green_engine.switch(deployment_id)

            except (KeyError, ValueError):
                pass

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


def build_default_governance_progressive_delivery_engine() -> (
    ProgressiveDeliveryEngine
):
    """
    Build the process-wide progressive delivery engine, wired to the
    process-wide governance event bus, scheduler, traffic router, and
    the three per-strategy deployment engines.
    """

    from .deployment_governance_blue_green import get_blue_green_engine
    from .deployment_governance_canary import get_canary_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rolling import get_rolling_engine
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_traffic_router import get_traffic_router

    return ProgressiveDeliveryEngine(
        event_bus=get_event_bus(),
        canary_engine=get_canary_engine(),
        rolling_engine=get_rolling_engine(),
        blue_green_engine=get_blue_green_engine(),
        scheduler=get_scheduler(),
        traffic_router=get_traffic_router(),
    )


# Shared for the lifetime of the process: which deployments have an
# active progressive delivery pipeline, and their stage progression/
# history, is inherently process-wide, not something that can be
# meaningfully rebuilt fresh per request.
_progressive_delivery_engine = (
    build_default_governance_progressive_delivery_engine()
)


def get_progressive_delivery_engine() -> ProgressiveDeliveryEngine:
    """
    Return the process-wide progressive delivery engine.
    """

    return _progressive_delivery_engine
