from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_blue_green import (
        BlueGreenDeploymentEngine,
    )
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )

# The lifecycle every rollout passes through: PENDING the instant it
# is created, RUNNING once started (with PAUSED as a reversible detour
# in between), then exactly one of the three terminal states.
ROLLOUT_STATES: "tuple[str, ...]" = (
    "PENDING",
    "RUNNING",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)

_TERMINAL_STATES: "frozenset[str]" = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED"}
)

# The strategies a rollout may be created with. Not enforced beyond
# membership — orchestrating the actual traffic transition for a given
# strategy is out of scope for this manager, which only tracks
# lifecycle and state.
ROLLOUT_STRATEGIES: "tuple[str, ...]" = (
    "BLUE_GREEN",
    "CANARY",
    "ROLLING",
    "PROGRESSIVE",
)

_VALID_TRANSITIONS: "dict[str, frozenset[str]]" = {
    "PENDING": frozenset({"RUNNING", "CANCELLED", "FAILED"}),
    "RUNNING": frozenset(
        {"PAUSED", "COMPLETED", "FAILED", "CANCELLED"}
    ),
    "PAUSED": frozenset({"RUNNING", "CANCELLED", "FAILED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}


@dataclass(frozen=True)
class Rollout:
    """
    A rollout's identity and the strategy it was created with.
    Immutable — state.py transitions produce a fresh Rollout rather
    than mutating this one, matching JobExecution/ExecutionResult in
    deployment_governance_execution_manager.
    """

    rollout_id: str

    deployment_id: str

    strategy: str

    state: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.rollout_id:
            raise ValueError("rollout_id must not be empty")

        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.strategy not in ROLLOUT_STRATEGIES:
            raise ValueError(
                f"strategy must be one of {ROLLOUT_STRATEGIES}"
            )

        if self.state not in ROLLOUT_STATES:
            raise ValueError(f"state must be one of {ROLLOUT_STATES}")

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "rollout_id": self.rollout_id,
            "deployment_id": self.deployment_id,
            "strategy": self.strategy,
            "state": self.state,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RolloutStatus:
    """
    An immutable point-in-time snapshot of one rollout's progress.
    """

    rollout_id: str

    state: str

    progress: float

    current_stage: str

    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.rollout_id:
            raise ValueError("rollout_id must not be empty")

        if self.state not in ROLLOUT_STATES:
            raise ValueError(f"state must be one of {ROLLOUT_STATES}")

        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("progress must be between 0.0 and 1.0")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "rollout_id": self.rollout_id,
            "state": self.state,
            "progress": self.progress,
            "current_stage": self.current_stage,
            "updated_at": self.updated_at.isoformat(),
        }


class DeploymentRolloutManager:
    """
    Orchestrates deployment rollout lifecycle: creation, start/pause/
    resume/cancel/complete transitions, and status/listing.

    Coordinating the actual traffic transition for a given strategy
    (Blue/Green, Canary, Rolling, Progressive) is out of scope here —
    this manager tracks rollout identity, lifecycle state, and
    progress, and publishes the events other governance components
    (and API callers) observe that lifecycle through.

    Thread-safe: every mutation of the rollout registry is guarded by
    an internal lock. Lifecycle operations (start/pause/resume/cancel/
    complete) are idempotent: reapplying an operation that would
    re-enter the rollout's current state is a no-op returning the
    unchanged Rollout rather than raising.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
        blue_green_engine: "BlueGreenDeploymentEngine | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._rollouts: "dict[str, Rollout]" = {}

        self._status: "dict[str, RolloutStatus]" = {}

        self._active_deployment_ids: "set[str]" = set()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._version_registry = version_registry

        self._blue_green_engine = blue_green_engine

    def create(
        self, deployment_id: str, strategy: str
    ) -> Rollout:
        """
        Create a new PENDING rollout for deployment_id.

        Raises ValueError if deployment_id already has an active
        (non-terminal) rollout, if strategy is not one of
        ROLLOUT_STRATEGIES, or — when this manager was built with a
        version_registry — if deployment_id does not resolve to a
        currently registered deployment there. With no version_registry
        wired in, deployment_id is accepted as given, unresolved
        against anything: the manager tracks rollout lifecycle either
        way, it just cannot vouch that the deployment itself exists.
        """

        if not deployment_id:
            raise ValueError("deployment_id must not be empty")

        if (
            self._version_registry is not None
            and not self._version_registry.exists(deployment_id)
        ):
            raise ValueError(
                f"deployment '{deployment_id}' is not registered in "
                "the version registry"
            )

        with self._lock:
            if deployment_id in self._active_deployment_ids:
                raise ValueError(
                    f"deployment '{deployment_id}' already has an "
                    "active rollout"
                )

            rollout_id = str(uuid4())
            now = self._clock()

            rollout = Rollout(
                rollout_id=rollout_id,
                deployment_id=deployment_id,
                strategy=strategy,
                state="PENDING",
                created_at=now,
            )

            self._rollouts[rollout_id] = rollout
            self._active_deployment_ids.add(deployment_id)

            self._status[rollout_id] = RolloutStatus(
                rollout_id=rollout_id,
                state="PENDING",
                progress=0.0,
                current_stage="created",
                updated_at=now,
            )

        self._publish(
            "rollout_created",
            rollout_id,
            {
                "deployment_id": deployment_id,
                "strategy": strategy,
            },
        )

        return rollout

    def _transition(
        self,
        rollout_id: str,
        *,
        to_state: str,
        stage: str,
        progress: "float | None" = None,
        event_type: str,
        from_states: "frozenset[str] | None" = None,
    ) -> Rollout:
        with self._lock:
            rollout = self._rollouts.get(rollout_id)

            if rollout is None:
                raise KeyError(
                    f"rollout '{rollout_id}' is not registered"
                )

            if rollout.state == to_state:
                return rollout

            allowed = _VALID_TRANSITIONS[rollout.state]

            # from_states narrows the generic state-pair table for
            # operations (pause/resume) that share a target state
            # (RUNNING) with another operation (start/resume) but must
            # not be reachable from every state that table would
            # otherwise permit — e.g. resume() targets RUNNING just
            # like start() does, but must only fire from PAUSED, not
            # from PENDING.
            if to_state not in allowed or (
                from_states is not None
                and rollout.state not in from_states
            ):
                raise ValueError(
                    f"cannot transition rollout '{rollout_id}' from "
                    f"'{rollout.state}' to '{to_state}'"
                )

            now = self._clock()

            updated = replace(rollout, state=to_state)

            self._rollouts[rollout_id] = updated

            previous_status = self._status[rollout_id]

            self._status[rollout_id] = RolloutStatus(
                rollout_id=rollout_id,
                state=to_state,
                progress=(
                    previous_status.progress
                    if progress is None
                    else progress
                ),
                current_stage=stage,
                updated_at=now,
            )

            if to_state in _TERMINAL_STATES:
                self._active_deployment_ids.discard(
                    updated.deployment_id
                )

        self._publish(
            event_type,
            rollout_id,
            {"deployment_id": updated.deployment_id},
        )

        return updated

    def start(self, rollout_id: str) -> Rollout:
        """
        Transition rollout_id from PENDING to RUNNING.

        Idempotent: a no-op returning the unchanged Rollout if already
        RUNNING. Raises KeyError if rollout_id is not registered, or
        ValueError if the current state cannot transition to RUNNING.
        """

        return self._transition(
            rollout_id,
            to_state="RUNNING",
            stage="in_progress",
            progress=0.0,
            event_type="rollout_started",
        )

    def pause(self, rollout_id: str) -> Rollout:
        """
        Transition rollout_id from RUNNING to PAUSED.

        Idempotent: a no-op returning the unchanged Rollout if already
        PAUSED.
        """

        return self._transition(
            rollout_id,
            to_state="PAUSED",
            stage="paused",
            event_type="rollout_paused",
        )

    def resume(self, rollout_id: str) -> Rollout:
        """
        Transition rollout_id from PAUSED back to RUNNING.

        Idempotent: a no-op returning the unchanged Rollout if already
        RUNNING.
        """

        return self._transition(
            rollout_id,
            to_state="RUNNING",
            stage="in_progress",
            event_type="rollout_resumed",
            from_states=frozenset({"PAUSED"}),
        )

    def cancel(self, rollout_id: str) -> Rollout:
        """
        Transition rollout_id to CANCELLED from any non-terminal
        state.

        Idempotent: a no-op returning the unchanged Rollout if already
        CANCELLED.
        """

        return self._transition(
            rollout_id,
            to_state="CANCELLED",
            stage="cancelled",
            event_type="rollout_cancelled",
        )

    def complete(self, rollout_id: str) -> Rollout:
        """
        Transition rollout_id from RUNNING to COMPLETED.

        Idempotent: a no-op returning the unchanged Rollout if already
        COMPLETED. For a strategy="BLUE_GREEN" rollout with a
        blue_green_engine wired in, completing it for the first time
        also asks that engine to switch traffic
        (BlueGreenDeploymentEngine.switch) for this rollout's
        deployment_id — completing a Blue/Green rollout is what
        actually cuts traffic over. If the engine has nothing staged
        for this deployment_id (it was never deploy()'d/validate()'d
        through the engine directly), that delegation is silently
        skipped rather than failing rollout completion.
        """

        was_already_completed = self.status(rollout_id).state == (
            "COMPLETED"
        )

        rollout = self._transition(
            rollout_id,
            to_state="COMPLETED",
            stage="completed",
            progress=1.0,
            event_type="rollout_completed",
        )

        if (
            not was_already_completed
            and rollout.strategy == "BLUE_GREEN"
            and self._blue_green_engine is not None
        ):
            try:
                self._blue_green_engine.switch(rollout.deployment_id)

            except (KeyError, ValueError):
                pass

        return rollout

    def fail(
        self, rollout_id: str, reason: "str | None" = None
    ) -> Rollout:
        """
        Transition rollout_id to FAILED from any non-terminal state.

        Idempotent: a no-op returning the unchanged Rollout if already
        FAILED. Not part of the core API surface (there is no
        POST .../fail endpoint) but available for other governance
        components — e.g. a future health check — to report a rollout
        as failed.
        """

        rollout = self._transition(
            rollout_id,
            to_state="FAILED",
            stage="failed",
            event_type="rollout_failed",
        )

        if reason is not None:
            self._publish(
                "rollout_failed", rollout_id, {"reason": reason}
            )

        return rollout

    def status(self, rollout_id: str) -> RolloutStatus:
        """
        Return rollout_id's current immutable status snapshot.

        Raises KeyError if rollout_id is not registered.
        """

        with self._lock:
            status = self._status.get(rollout_id)

            if status is None:
                raise KeyError(
                    f"rollout '{rollout_id}' is not registered"
                )

            return status

    def get(self, rollout_id: str) -> Rollout:
        """
        Return rollout_id's current Rollout record.

        Raises KeyError if rollout_id is not registered.
        """

        with self._lock:
            rollout = self._rollouts.get(rollout_id)

            if rollout is None:
                raise KeyError(
                    f"rollout '{rollout_id}' is not registered"
                )

            return rollout

    def list(self) -> "tuple[Rollout, ...]":
        """
        Return every registered rollout, ordered deterministically by
        created_at then rollout_id.
        """

        with self._lock:
            rollouts = list(self._rollouts.values())

        return tuple(
            sorted(
                rollouts,
                key=lambda rollout: (
                    rollout.created_at,
                    rollout.rollout_id,
                ),
            )
        )

    def clear(self) -> None:
        """
        Remove every registered rollout.
        """

        with self._lock:
            self._rollouts.clear()
            self._status.clear()
            self._active_deployment_ids.clear()

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


def build_default_governance_rollout_manager() -> (
    DeploymentRolloutManager
):
    """
    Build the process-wide governance rollout manager, wired to the
    process-wide governance event bus, deployment version registry,
    and Blue/Green deployment engine.
    """

    from .deployment_governance_blue_green import get_blue_green_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return DeploymentRolloutManager(
        event_bus=get_event_bus(),
        version_registry=get_version_registry(),
        blue_green_engine=get_blue_green_engine(),
    )


# Shared for the lifetime of the process: the rollout registry (which
# deployments currently have an active rollout, and each rollout's
# state) is inherently process-wide, not something that can be
# meaningfully rebuilt fresh per request.
_rollout_manager = build_default_governance_rollout_manager()


def get_rollout_manager() -> DeploymentRolloutManager:
    """
    Return the process-wide governance rollout manager.
    """

    return _rollout_manager
