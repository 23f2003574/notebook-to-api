from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_version_registry import is_semantic_version

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_blue_green import (
        BlueGreenDeploymentEngine,
    )
    from .deployment_governance_canary import CanaryDeploymentEngine
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_progressive_delivery import (
        ProgressiveDeliveryEngine,
    )
    from .deployment_governance_rolling import RollingDeploymentEngine
    from .deployment_governance_rollout_manager import (
        DeploymentRolloutManager,
    )
    from .deployment_governance_rollout_policy import (
        DeploymentRolloutPolicyEngine,
    )
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )

# The rollback triggers this engine ships built-in validation for.
# Purely descriptive labels a caller attaches to a plan explaining why
# it exists — register_trigger() extends this set for custom
# triggers, the same shape as DeploymentTrafficRouter.register_strategy.
ROLLBACK_TRIGGERS: "tuple[str, ...]" = (
    "HEALTH_CHECK_FAILURE",
    "ERROR_RATE_THRESHOLD",
    "CRASH_LOOP_DETECTED",
    "MANUAL_ROLLBACK_REQUEST",
    "POLICY_VIOLATION",
    "TIMEOUT_EXCEEDED",
)

_NON_REMOVED_STATES: "frozenset[str]" = frozenset(
    {"REGISTERED", "UPDATED"}
)

_ROLLOUT_TERMINAL_STATES: "frozenset[str]" = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED"}
)


@dataclass(frozen=True)
class RollbackPlan:
    """
    A decision to roll deployment_id back to target_version, and why.
    """

    deployment_id: str

    target_version: str

    trigger: str

    automatic: bool

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not is_semantic_version(self.target_version):
            raise ValueError(
                f"target_version '{self.target_version}' is not a "
                "valid semantic version"
            )

        if not self.trigger:
            raise ValueError("trigger must not be empty")

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "target_version": self.target_version,
            "trigger": self.trigger,
            "automatic": self.automatic,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RollbackResult:
    """
    The immutable, terminal outcome of one execute() call.
    """

    deployment_id: str

    previous_version: str

    restored_version: str

    success: bool

    completed_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not self.restored_version:
            raise ValueError("restored_version must not be empty")

        if self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "previous_version": self.previous_version,
            "restored_version": self.restored_version,
            "success": self.success,
            "completed_at": self.completed_at.isoformat(),
        }


class DeploymentRollbackEngine:
    """
    Plans and executes rollbacks to a deployment's last known healthy
    version, coordinating the existing rollout machinery rather than
    implementing any deployment logic of its own: execute() shifts
    traffic back via the DeploymentTrafficRouter, cancels whatever
    rollout is still in flight through the DeploymentRolloutManager,
    and asks each of BlueGreenDeploymentEngine, CanaryDeploymentEngine,
    RollingDeploymentEngine, and ProgressiveDeliveryEngine to roll
    back deployment_id too, best effort, for whichever of them
    actually has something active there.

    If an event_bus is wired in, this engine subscribes itself to
    "rollout_failed" and "rollout_health_critical" events and
    automatically plans + executes a rollback in response — the
    "continuously monitors rollout outcomes" behavior described in
    this engine's purpose — rather than DeploymentRolloutManager or
    DeploymentRolloutHealthEngine calling into this engine directly.
    That direction was deliberate: DeploymentRolloutManager is a
    dependency of this engine (for cancelling in-flight rollouts), so
    having it also depend on this engine for construction would be a
    circular singleton dependency; reacting to its already-published
    events avoids that while still deliverying the "trigger it
    automatically" requirement in Runtime Integration.

    target_version, when omitted from create_plan(), is resolved as
    the version immediately prior to whatever is currently registered
    for deployment_id in the Version Registry — "the last known
    healthy deployment" this engine's own description promises.

    Thread-safe: every mutation of engine state is guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        rollout_manager: "DeploymentRolloutManager | None" = None,
        blue_green_engine: "BlueGreenDeploymentEngine | None" = None,
        canary_engine: "CanaryDeploymentEngine | None" = None,
        rolling_engine: "RollingDeploymentEngine | None" = None,
        progressive_engine: "ProgressiveDeliveryEngine | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
        policy_engine: "DeploymentRolloutPolicyEngine | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._plans: "dict[str, RollbackPlan]" = {}

        self._active_deployment_ids: "set[str]" = set()

        self._results: "dict[str, RollbackResult]" = {}

        self._history: "dict[str, list[RollbackResult]]" = {}

        self._triggers: "set[str]" = set(ROLLBACK_TRIGGERS)

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._version_registry = version_registry

        self._traffic_router = traffic_router

        self._rollout_manager = rollout_manager

        self._blue_green_engine = blue_green_engine

        self._canary_engine = canary_engine

        self._rolling_engine = rolling_engine

        self._progressive_engine = progressive_engine

        self._audit_service = audit_service

        self._policy_engine = policy_engine

        if self._event_bus is not None:
            self._event_bus.subscribe(
                "rollout_failed", self._on_automatic_rollback_trigger
            )
            self._event_bus.subscribe(
                "rollout_health_critical", self._on_automatic_rollback_trigger
            )

    def register_trigger(self, name: str) -> None:
        """
        Register name as a recognized rollback trigger, the trigger
        registry future custom triggers plug into.
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            self._triggers.add(name)

    def create_plan(
        self,
        deployment_id: str,
        target_version: "str | None" = None,
        trigger: str = "MANUAL_ROLLBACK_REQUEST",
        automatic: bool = False,
    ) -> RollbackPlan:
        """
        Plan a rollback for deployment_id to target_version.

        If target_version is omitted, it is resolved as the version
        registered immediately before deployment_id's current one, per
        the Version Registry's revision history.

        Raises ValueError if deployment_id already has an active
        rollback plan, if trigger is not registered (built in or via
        register_trigger), if target_version is omitted and cannot be
        resolved, or if the resolved/given target_version fails
        validate_target().
        """

        with self._lock:
            if deployment_id in self._active_deployment_ids:
                raise ValueError(
                    f"deployment '{deployment_id}' already has an "
                    "active rollback plan"
                )

            if trigger not in self._triggers:
                raise ValueError(
                    f"trigger '{trigger}' is not registered"
                )

        resolved_target = (
            target_version
            if target_version is not None
            else self._resolve_target_version(deployment_id)
        )

        if not self.validate_target(deployment_id, resolved_target):
            raise ValueError(
                f"'{resolved_target}' is not a valid rollback target "
                f"for deployment '{deployment_id}'"
            )

        with self._lock:
            now = self._clock()

            plan = RollbackPlan(
                deployment_id=deployment_id,
                target_version=resolved_target,
                trigger=trigger,
                automatic=automatic,
                created_at=now,
            )

            self._plans[deployment_id] = plan
            self._active_deployment_ids.add(deployment_id)
            self._results.pop(deployment_id, None)
            self._history.setdefault(deployment_id, [])

        self._publish(
            "rollback_planned",
            deployment_id,
            {"target_version": resolved_target, "trigger": trigger},
        )

        if self._audit_service is not None:
            self._audit_service.record(
                action="rollback_planned",
                actor="deployment_rollback_engine",
                resource=deployment_id,
                outcome="planned",
                metadata=plan.to_dict(),
            )

        return plan

    def validate_target(
        self, deployment_id: str, target_version: str
    ) -> bool:
        """
        Whether target_version is a legitimate rollback target for
        deployment_id: it must appear (as a REGISTERED or UPDATED
        entry, not a REMOVED tombstone) in the Version Registry's
        revision history for deployment_id.

        Returns True unconditionally if no version_registry is wired
        in — this rule cannot be enforced without one, matching how
        DeploymentRolloutManager.create() treats a missing
        version_registry as "unresolved, not rejected."
        """

        if self._version_registry is None:
            return True

        history = self._version_registry.history(deployment_id)

        return any(
            revision.version == target_version
            for revision in history
            if revision.state in _NON_REMOVED_STATES
        )

    def set_policy_engine(
        self, policy_engine: "DeploymentRolloutPolicyEngine"
    ) -> None:
        """
        Wire policy_engine in after construction — see
        CanaryDeploymentEngine.set_health_engine for why this exists
        instead of a constructor-injected singleton (the process-wide
        rollout policy engine's own singleton depends, transitively
        through the analytics engine, on this one).
        """

        self._policy_engine = policy_engine

    def _policy_allows_execution(
        self, deployment_id: str, plan: RollbackPlan
    ) -> bool:
        if self._policy_engine is None:
            return True

        decision = self._policy_engine.evaluate(
            deployment_id, "rollback_execution",
            {"trigger": plan.trigger, "automatic": plan.automatic},
        )

        return decision.allowed

    def execute(self, deployment_id: str) -> RollbackResult:
        """
        Execute deployment_id's current rollback plan.

        Idempotent: calling this again after a plan has already been
        executed returns the same RollbackResult without repeating any
        of the coordination below.

        A wired policy_engine denying the "rollback_execution" action
        is treated the same as an invalid target: a FAILED
        RollbackResult is recorded and returned rather than raised —
        this engine is meant to be usable unattended (see the
        automatic-trigger event subscriptions above), so a caller
        needs a result object to inspect either way, not an exception
        to catch.

        Raises KeyError if deployment_id has no rollback plan, or
        ValueError if its plan is not active (already cancelled).
        """

        with self._lock:
            plan = self._plans.get(deployment_id)

            if plan is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rollback "
                    "plan"
                )

            cached = self._results.get(deployment_id)

            if cached is not None:
                return cached

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"rollback plan for '{deployment_id}' is not "
                    "active"
                )

        self._publish("rollback_started", deployment_id, {})

        previous_version = self._current_registered_version(
            deployment_id
        )

        target_valid = self.validate_target(
            deployment_id, plan.target_version
        )

        policy_allowed = self._policy_allows_execution(
            deployment_id, plan
        )

        now = self._clock()

        if not target_valid or not policy_allowed:
            result = RollbackResult(
                deployment_id=deployment_id,
                previous_version=previous_version,
                restored_version=plan.target_version,
                success=False,
                completed_at=now,
            )

            self._finalize(deployment_id, result)

            self._publish("rollback_failed", deployment_id, {})

            self._record_audit(
                "rollback_failed", deployment_id, result,
            )

            return result

        self._route_traffic(deployment_id, plan.target_version)
        self._cancel_active_rollout(deployment_id)
        self._rollback_strategy_engines(deployment_id)

        result = RollbackResult(
            deployment_id=deployment_id,
            previous_version=previous_version,
            restored_version=plan.target_version,
            success=True,
            completed_at=now,
        )

        self._finalize(deployment_id, result)

        self._publish("rollback_completed", deployment_id, {})

        self._record_audit("rollback_completed", deployment_id, result)

        return result

    def cancel(self, deployment_id: str) -> RollbackPlan:
        """
        Cancel deployment_id's active rollback plan without executing
        it.

        Idempotent: a no-op (still returning the current plan) if
        already cancelled. Raises KeyError if deployment_id has no
        rollback plan, or ValueError if its plan has already been
        executed (nothing left to cancel).
        """

        with self._lock:
            plan = self._plans.get(deployment_id)

            if plan is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rollback "
                    "plan"
                )

            if deployment_id in self._results:
                raise ValueError(
                    f"rollback plan for '{deployment_id}' has already "
                    "been executed"
                )

            already_cancelled = (
                deployment_id not in self._active_deployment_ids
            )

            self._active_deployment_ids.discard(deployment_id)

        if not already_cancelled:
            self._publish("rollback_cancelled", deployment_id, {})

        return plan

    def status(self, deployment_id: str) -> RollbackPlan:
        """
        Return deployment_id's current (or most recent) rollback plan.

        Raises KeyError if deployment_id has never had a plan created.
        """

        with self._lock:
            plan = self._plans.get(deployment_id)

            if plan is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rollback "
                    "plan"
                )

            return plan

    def latest(self, deployment_id: str) -> RollbackResult:
        """
        Return deployment_id's most recently executed rollback result.

        Raises KeyError if deployment_id's current plan has never been
        executed.
        """

        with self._lock:
            result = self._results.get(deployment_id)

            if result is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no executed "
                    "rollback"
                )

            return result

    def history(self, deployment_id: str) -> "tuple[RollbackResult, ...]":
        """
        Return every rollback result ever recorded for deployment_id,
        oldest first, across every plan. Returns an empty tuple if
        deployment_id has never had a plan created.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def list(self) -> "tuple[RollbackPlan, ...]":
        """
        Return every currently tracked deployment's rollback plan,
        ordered deterministically by deployment_id.
        """

        with self._lock:
            plans = list(self._plans.values())

        return tuple(
            sorted(plans, key=lambda plan: plan.deployment_id)
        )

    def clear_history(self) -> None:
        """
        Remove every tracked rollback plan, result, and history entry.
        """

        with self._lock:
            self._plans.clear()
            self._active_deployment_ids.clear()
            self._results.clear()
            self._history.clear()

    def _resolve_target_version(self, deployment_id: str) -> str:
        if self._version_registry is None:
            raise ValueError(
                "target_version must be provided when no "
                "version_registry is wired"
            )

        history = self._version_registry.history(deployment_id)

        distinct_versions: "list[str]" = []

        for revision in history:
            if revision.state not in _NON_REMOVED_STATES:
                continue

            if (
                not distinct_versions
                or distinct_versions[-1] != revision.version
            ):
                distinct_versions.append(revision.version)

        if len(distinct_versions) < 2:
            raise ValueError(
                f"deployment '{deployment_id}' has no previous "
                "version to roll back to"
            )

        return distinct_versions[-2]

    def _current_registered_version(self, deployment_id: str) -> str:
        if self._version_registry is None:
            return ""

        try:
            return self._version_registry.get(deployment_id).version

        except KeyError:
            return ""

    def _finalize(
        self, deployment_id: str, result: RollbackResult
    ) -> None:
        with self._lock:
            self._results[deployment_id] = result
            self._history.setdefault(deployment_id, []).append(result)
            self._active_deployment_ids.discard(deployment_id)

    def _route_traffic(
        self, deployment_id: str, target_version: str
    ) -> None:
        if self._traffic_router is None:
            return

        try:
            self._traffic_router.allocate(
                deployment_id, target_version, 100.0
            )

        except (KeyError, ValueError):
            pass

    def _cancel_active_rollout(self, deployment_id: str) -> None:
        if self._rollout_manager is None:
            return

        for rollout in self._rollout_manager.list():
            if rollout.deployment_id != deployment_id:
                continue

            if rollout.state in _ROLLOUT_TERMINAL_STATES:
                continue

            try:
                self._rollout_manager.cancel(rollout.rollout_id)

            except (KeyError, ValueError):
                pass

    def _rollback_strategy_engines(self, deployment_id: str) -> None:
        for engine in (
            self._blue_green_engine,
            self._canary_engine,
            self._rolling_engine,
            self._progressive_engine,
        ):
            if engine is None:
                continue

            try:
                engine.rollback(deployment_id)

            except (KeyError, ValueError):
                pass

    def _record_audit(
        self, action: str, deployment_id: str, result: RollbackResult
    ) -> None:
        if self._audit_service is None:
            return

        self._audit_service.record(
            action=action,
            actor="deployment_rollback_engine",
            resource=deployment_id,
            outcome="success" if result.success else "failure",
            metadata=result.to_dict(),
        )

    def _on_automatic_rollback_trigger(self, event: Any) -> None:
        """
        Shared handler for both subscriptions in __init__:
        "rollout_failed" (published by DeploymentRolloutManager.fail())
        and "rollout_health_critical" (published by
        DeploymentRolloutHealthEngine) both mean the same thing to
        this engine — plan and execute an automatic rollback for
        whatever deployment_id the event names.
        """

        deployment_id = event.payload.get("deployment_id")

        if not deployment_id:
            return

        try:
            self.create_plan(
                deployment_id,
                trigger="HEALTH_CHECK_FAILURE",
                automatic=True,
            )
            self.execute(deployment_id)

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


def build_default_governance_rollback_engine() -> DeploymentRollbackEngine:
    """
    Build the process-wide automated rollback engine, wired to the
    process-wide governance event bus, version registry, traffic
    router, rollout manager, the three per-strategy deployment
    engines, and audit service.

    Deliberately one-directional: DeploymentRolloutManager's own
    build_default does not wire a rollback_engine back — see this
    class's docstring for why (avoiding a circular singleton
    dependency, resolved instead by this engine subscribing to
    "rollout_failed" on the event bus).
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_blue_green import get_blue_green_engine
    from .deployment_governance_canary import get_canary_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_progressive_delivery import (
        get_progressive_delivery_engine,
    )
    from .deployment_governance_rolling import get_rolling_engine
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_traffic_router import get_traffic_router
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return DeploymentRollbackEngine(
        event_bus=get_event_bus(),
        version_registry=get_version_registry(),
        traffic_router=get_traffic_router(),
        rollout_manager=get_rollout_manager(),
        blue_green_engine=get_blue_green_engine(),
        canary_engine=get_canary_engine(),
        rolling_engine=get_rolling_engine(),
        progressive_engine=get_progressive_delivery_engine(),
        audit_service=get_audit_service(),
    )


# Shared for the lifetime of the process: which deployments have an
# active rollback plan, and their execution history, is inherently
# process-wide, not something that can be meaningfully rebuilt fresh
# per request.
_rollback_engine = build_default_governance_rollback_engine()


def get_rollback_engine() -> DeploymentRollbackEngine:
    """
    Return the process-wide automated rollback engine.
    """

    return _rollback_engine
