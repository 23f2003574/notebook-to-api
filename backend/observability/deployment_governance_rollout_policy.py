from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_rules import conditions_match

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rbac import DeploymentRBACEngine
    from .deployment_governance_rollout_analytics import (
        DeploymentRolloutAnalytics,
    )

# The built-in rollout checks this engine understands natively,
# selectable by name via register()'s policy_type parameter — the
# same plug-in shape GovernanceSchedulerPolicyEngine offers via its
# own BUILT_IN_SCHEDULER_POLICIES, so a new rollout rule never
# requires modifying this engine.
BUILT_IN_ROLLOUT_POLICIES: "tuple[str, ...]" = (
    "max_concurrent_rollouts",
    "required_health_score",
    "max_rollback_rate",
    "deployment_freeze_window",
    "strategy_allow_list",
    "approval_required",
    "target_environment_restriction",
)

# The points in a rollout's lifecycle Runtime Integration says must be
# policy-evaluated. Not enforced as a closed set by evaluate() itself
# (any non-empty action string is accepted) — this is what the built-
# in policies and callers are expected to pass, matching how
# GOVERNANCE_EVENT_TYPES documents a vocabulary without the event bus
# enforcing membership.
ROLLOUT_POLICY_ACTIONS: "tuple[str, ...]" = (
    "rollout_creation",
    "rollout_start",
    "rollout_promotion",
    "traffic_shift",
    "rollback_execution",
    "rollout_completion",
)

RolloutPolicyEvaluator = Callable[
    ["RolloutPolicy", "dict[str, Any]"], "tuple[bool, str | None]"
]


@dataclass(frozen=True)
class RolloutPolicy:
    """
    A named rollout policy. strategy, when given, scopes this policy
    to rollouts of that one strategy (e.g. "CANARY") — evaluate()
    skips it entirely for any other strategy; None applies
    universally, matching how a SchedulerPolicy always applies to
    every job.
    """

    name: str

    priority: int

    enabled: bool

    strategy: "str | None" = None

    conditions: "dict[str, Any]" = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        object.__setattr__(
            self, "conditions", MappingProxyType(dict(self.conditions))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "priority": self.priority,
            "enabled": self.enabled,
            "strategy": self.strategy,
            "conditions": dict(self.conditions),
        }


@dataclass(frozen=True)
class RolloutPolicyDecision:
    """
    The immutable outcome of evaluating one rollout action against
    every registered rollout policy.
    """

    allowed: bool

    policy: "str | None"

    action: str

    reason: "str | None"

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("action must not be empty")

        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")

        if self.allowed:
            if self.policy is not None or self.reason is not None:
                raise ValueError(
                    "policy and reason must not be set when allowed "
                    "is True"
                )

        else:
            if self.policy is None or self.reason is None:
                raise ValueError(
                    "policy and reason must be set when allowed is "
                    "False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "policy": self.policy,
            "action": self.action,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class DeploymentRolloutPolicyEngine:
    """
    Governs whether a specific rollout *action* (creation, start,
    promotion, a traffic shift, a rollback execution, or completion —
    ROLLOUT_POLICY_ACTIONS) is currently allowed for one deployment —
    distinct from GovernancePolicyEngine (governance operations like
    lifecycle transitions) and GovernanceSchedulerPolicyEngine
    (whether a job may run right now): neither knows what a rollout
    strategy or health score is.

    Evaluation is default-allow, the same shape as both of those: a
    request is denied only if some enabled, in-scope policy matches,
    in priority-then-name order, and the first match short-circuits
    the rest. "In scope" means policy.strategy is either None
    (universal) or equal to context["strategy"]. A policy matches via
    its own built-in or custom evaluator (register()'s policy_type/
    evaluator parameters), or — with neither given — by plain
    conditions-match against the context.

    Every evaluate() call publishes an event and records an audit
    entry (both optional, no-ops if not wired). If an analytics engine
    is wired in, this engine also registers a "rollout_policy_denial_
    rate" KPI into it at construction time — computed from this
    engine's own running decision/denial counters — the "update
    rollout analytics" requirement, achieved by pushing a metric into
    DeploymentRolloutAnalytics's existing extension point rather than
    this engine reaching into analytics' internals.

    Thread-safe: every mutation of the policy registry (and the
    decision counters behind the analytics KPI) is guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
        analytics: "DeploymentRolloutAnalytics | None" = None,
        rbac_engine: "DeploymentRBACEngine | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._policies: "dict[str, RolloutPolicy]" = {}

        self._evaluators: "dict[str, RolloutPolicyEvaluator | None]" = {}

        self._decision_count = 0

        self._denial_count = 0

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._audit_service = audit_service

        self._analytics = analytics

        self._rbac_engine = rbac_engine

        if self._analytics is not None:
            self._analytics.register_kpi(
                "rollout_policy_denial_rate", self._denial_rate
            )

    def register(
        self,
        name: str,
        *,
        priority: int = 0,
        enabled: bool = True,
        strategy: "str | None" = None,
        conditions: "dict[str, Any] | None" = None,
        policy_type: "str | None" = None,
        evaluator: "RolloutPolicyEvaluator | None" = None,
        principal_id: "str | None" = None,
    ) -> RolloutPolicy:
        """
        Register a new named policy.

        If evaluator is given, it is used directly (a custom policy).
        Otherwise, if policy_type names one of
        BUILT_IN_ROLLOUT_POLICIES, that built-in check is used. With
        neither given, the policy falls back to plain conditions-match
        against evaluate()'s context.

        Raises ValueError if name is already registered, or if
        policy_type is given but not a recognized built-in. With
        principal_id given and an rbac_engine wired in, also raises
        PermissionError if principal_id is not authorized for
        "policy.manage".
        """

        self._check_authorization(principal_id, "policy.manage")

        with self._lock:
            if name in self._policies:
                raise ValueError(
                    f"policy '{name}' is already registered"
                )

            if evaluator is None and policy_type is not None:
                evaluator = self._built_in_evaluators().get(policy_type)

                if evaluator is None:
                    raise ValueError(
                        f"unknown built-in rollout policy type "
                        f"'{policy_type}'"
                    )

            policy = RolloutPolicy(
                name=name,
                priority=priority,
                enabled=enabled,
                strategy=strategy,
                conditions=conditions or {},
            )

            self._policies[name] = policy
            self._evaluators[name] = evaluator

        self._publish(
            "rollout_policy_registered", name, policy.to_dict()
        )

        return policy

    def remove(
        self, name: str, *, principal_id: "str | None" = None
    ) -> None:
        """
        Remove a registered policy.

        Raises KeyError if name is not registered. With principal_id
        given and an rbac_engine wired in, also raises PermissionError
        if principal_id is not authorized for "policy.manage".
        """

        self._check_authorization(principal_id, "policy.manage")

        with self._lock:
            if name not in self._policies:
                raise KeyError(f"policy '{name}' is not registered")

            del self._policies[name]
            self._evaluators.pop(name, None)

        self._publish("rollout_policy_removed", name, {})

    def enable(
        self, name: str, *, principal_id: "str | None" = None
    ) -> RolloutPolicy:
        """
        Enable a registered policy, returning its updated state.

        Raises KeyError if name is not registered. Idempotent. With
        principal_id given and an rbac_engine wired in, also raises
        PermissionError if principal_id is not authorized for
        "policy.manage".
        """

        self._check_authorization(principal_id, "policy.manage")

        return self._set_enabled(name, True)

    def disable(
        self, name: str, *, principal_id: "str | None" = None
    ) -> RolloutPolicy:
        """
        Disable a registered policy, returning its updated state.

        Raises KeyError if name is not registered. Idempotent. With
        principal_id given and an rbac_engine wired in, also raises
        PermissionError if principal_id is not authorized for
        "policy.manage".
        """

        self._check_authorization(principal_id, "policy.manage")

        return self._set_enabled(name, False)

    def evaluate(
        self,
        deployment_id: str,
        action: str,
        context: "dict[str, Any] | None" = None,
    ) -> RolloutPolicyDecision:
        """
        Evaluate action for deployment_id against every enabled,
        in-scope policy (see class docstring for scoping), in
        priority-then-name order, returning a deny decision for the
        first policy that matches or an allow decision if none match.

        Raises ValueError if action is empty.
        """

        if not action:
            raise ValueError("action must not be empty")

        context = context or {}
        strategy = context.get("strategy")

        for policy in self.list():
            if not policy.enabled:
                continue

            if policy.strategy is not None and policy.strategy != strategy:
                continue

            evaluator = self._evaluators.get(policy.name)

            if evaluator is not None:
                matched, reason = evaluator(policy, context)

            else:
                matched = conditions_match(policy.conditions, context)
                reason = (
                    f"deployment '{deployment_id}' matched policy "
                    f"'{policy.name}'"
                    if matched
                    else None
                )

            if matched:
                decision = RolloutPolicyDecision(
                    allowed=False, policy=policy.name, action=action,
                    reason=reason, evaluated_at=self._clock(),
                )

                self._finalize_decision(deployment_id, decision)

                return decision

        decision = RolloutPolicyDecision(
            allowed=True, policy=None, action=action, reason=None,
            evaluated_at=self._clock(),
        )

        self._finalize_decision(deployment_id, decision)

        return decision

    def evaluate_all(
        self,
        action: str,
        contexts: "dict[str, dict[str, Any]] | None" = None,
    ) -> "tuple[RolloutPolicyDecision, ...]":
        """
        Evaluate action for every deployment_id in contexts (a
        deployment_id -> context mapping), in deployment_id order,
        returning one decision per deployment_id in that same
        deterministic order.
        """

        contexts = contexts or {}

        return tuple(
            self.evaluate(deployment_id, action, contexts[deployment_id])
            for deployment_id in sorted(contexts)
        )

    def list(self) -> "tuple[RolloutPolicy, ...]":
        """
        Return every registered policy, ordered deterministically by
        priority then name.
        """

        with self._lock:
            policies = list(self._policies.values())

        return tuple(
            sorted(
                policies,
                key=lambda policy: (policy.priority, policy.name),
            )
        )

    def clear(self) -> None:
        """
        Remove every registered policy and reset the decision
        counters the analytics KPI is derived from.
        """

        with self._lock:
            self._policies.clear()
            self._evaluators.clear()
            self._decision_count = 0
            self._denial_count = 0

    def set_rbac_engine(
        self, rbac_engine: "DeploymentRBACEngine"
    ) -> None:
        """
        Wire rbac_engine in after construction, matching how
        build_default_governance_rbac_engine wires the process-wide
        RBAC engine into this engine's own singleton.
        """

        self._rbac_engine = rbac_engine

    def _check_authorization(
        self, principal_id: "str | None", permission: str
    ) -> None:
        """
        Raise PermissionError if principal_id is given, an
        rbac_engine is wired, and principal_id is not authorized for
        permission. A no-op if principal_id is None (authorization was
        not requested) or no rbac_engine is wired.
        """

        if principal_id is None or self._rbac_engine is None:
            return

        decision = self._rbac_engine.authorize(principal_id, permission)

        if not decision.allowed:
            raise PermissionError(
                f"principal '{principal_id}' is not authorized for "
                f"'{permission}'"
            )

    def _denial_rate(self) -> float:
        with self._lock:
            if self._decision_count == 0:
                return 0.0

            return self._denial_count / self._decision_count

    def _finalize_decision(
        self, deployment_id: str, decision: RolloutPolicyDecision
    ) -> None:
        with self._lock:
            self._decision_count += 1

            if not decision.allowed:
                self._denial_count += 1

        event_type = (
            "rollout_policy_allowed"
            if decision.allowed
            else "rollout_policy_denied"
        )

        self._publish(event_type, deployment_id, decision.to_dict())

        if self._audit_service is not None:
            self._audit_service.record(
                action=event_type,
                actor="system",
                resource=deployment_id,
                outcome="success" if decision.allowed else "failure",
                metadata=decision.to_dict(),
            )

    def _set_enabled(self, name: str, enabled: bool) -> RolloutPolicy:
        with self._lock:
            policy = self._policies.get(name)

            if policy is None:
                raise KeyError(f"policy '{name}' is not registered")

            updated = replace(policy, enabled=enabled)
            self._policies[name] = updated

            return updated

    def _built_in_evaluators(
        self,
    ) -> "dict[str, RolloutPolicyEvaluator]":
        return {
            "max_concurrent_rollouts": (
                self._evaluate_max_concurrent_rollouts
            ),
            "required_health_score": self._evaluate_required_health_score,
            "max_rollback_rate": self._evaluate_max_rollback_rate,
            "deployment_freeze_window": (
                self._evaluate_deployment_freeze_window
            ),
            "strategy_allow_list": self._evaluate_strategy_allow_list,
            "approval_required": self._evaluate_approval_required,
            "target_environment_restriction": (
                self._evaluate_target_environment_restriction
            ),
        }

    def _evaluate_max_concurrent_rollouts(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        max_concurrent = policy.conditions.get("max_concurrent")

        if max_concurrent is None:
            return False, None

        active_rollouts = context.get("active_rollouts", 0)

        if active_rollouts >= max_concurrent:
            return True, (
                f"active_rollouts ({active_rollouts}) >= max_concurrent "
                f"({max_concurrent})"
            )

        return False, None

    def _evaluate_required_health_score(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        min_score = policy.conditions.get("min_score")

        if min_score is None:
            return False, None

        health_score = context.get("health_score")

        if health_score is None:
            return False, None

        if health_score < min_score:
            return True, (
                f"health_score ({health_score}) < min_score "
                f"({min_score})"
            )

        return False, None

    def _evaluate_max_rollback_rate(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        max_rate = policy.conditions.get("max_rate")

        if max_rate is None:
            return False, None

        rollback_rate = context.get("rollback_rate")

        if rollback_rate is None:
            return False, None

        if rollback_rate > max_rate:
            return True, (
                f"rollback_rate ({rollback_rate}) > max_rate "
                f"({max_rate})"
            )

        return False, None

    def _evaluate_deployment_freeze_window(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        start_hour = policy.conditions.get("start_hour")
        end_hour = policy.conditions.get("end_hour")

        if start_hour is None or end_hour is None:
            return False, None

        current_time = context.get("current_time") or self._clock()
        hour = current_time.hour

        if start_hour <= end_hour:
            within_window = start_hour <= hour < end_hour

        else:
            # A freeze window that wraps past midnight, e.g. 22 -> 6.
            within_window = hour >= start_hour or hour < end_hour

        if within_window:
            return True, (
                f"current hour {hour} is inside the deployment freeze "
                f"window [{start_hour}, {end_hour})"
            )

        return False, None

    def _evaluate_strategy_allow_list(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        allowed_strategies = policy.conditions.get("allowed_strategies")

        if not allowed_strategies:
            return False, None

        strategy = context.get("strategy")

        if strategy is not None and strategy not in allowed_strategies:
            return True, (
                f"strategy '{strategy}' is not in the allowed list "
                f"{list(allowed_strategies)}"
            )

        return False, None

    def _evaluate_approval_required(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        if not policy.conditions.get("required", True):
            return False, None

        if not context.get("approved", False):
            return True, "deployment requires approval"

        return False, None

    def _evaluate_target_environment_restriction(
        self, policy: RolloutPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        allowed_environments = policy.conditions.get(
            "allowed_environments"
        )

        if not allowed_environments:
            return False, None

        environment = context.get("environment")

        if (
            environment is not None
            and environment not in allowed_environments
        ):
            return True, (
                f"environment '{environment}' is not in the allowed "
                f"list {list(allowed_environments)}"
            )

        return False, None

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


def build_default_governance_rollout_policy_engine() -> (
    DeploymentRolloutPolicyEngine
):
    """
    Build the process-wide rollout policy engine, wired to the
    process-wide governance event bus, audit service, and rollout
    analytics engine.

    Also wires itself into the process-wide rollout manager, traffic
    router, and rollback engine via their set_policy_engine() —
    those three cannot wire this engine back via constructor
    injection (this engine already depends, transitively through the
    analytics engine, on all three), so this is done here instead,
    once every singleton in the chain already exists. See
    CanaryDeploymentEngine.set_health_engine for the same pattern.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rollback import get_rollback_engine
    from .deployment_governance_rollout_analytics import (
        get_rollout_analytics,
    )
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_traffic_router import get_traffic_router

    engine = DeploymentRolloutPolicyEngine(
        event_bus=get_event_bus(),
        audit_service=get_audit_service(),
        analytics=get_rollout_analytics(),
    )

    get_rollout_manager().set_policy_engine(engine)
    get_traffic_router().set_policy_engine(engine)
    get_rollback_engine().set_policy_engine(engine)

    return engine


# Shared for the lifetime of the process: policies registered through
# the API need to be enforced identically by every caller (and every
# rollout lifecycle checkpoint), which a persistence runtime built
# fresh per request cannot provide on its own.
_rollout_policy_engine = build_default_governance_rollout_policy_engine()


def get_rollout_policy_engine() -> DeploymentRolloutPolicyEngine:
    """
    Return the process-wide rollout policy engine.
    """

    return _rollout_policy_engine
