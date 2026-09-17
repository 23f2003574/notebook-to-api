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
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )

# The built-in scheduling checks this engine understands natively,
# selectable by name via register()'s policy_type parameter. A custom
# policy not in this tuple may still be registered by passing an
# explicit evaluator callable instead — the same plug-in point every
# other built-in-strategy set in this codebase (recovery strategies,
# trigger types, backoff strategies) offers, so a new scheduling rule
# never requires modifying this engine, let alone the Scheduler.
BUILT_IN_SCHEDULER_POLICIES: "tuple[str, ...]" = (
    "max_concurrent_jobs",
    "maintenance_mode",
    "job_enabled",
    "dependency_satisfied",
    "lock_acquired",
    "retry_limit_not_exceeded",
    "execution_window_allowed",
    "bootstrap_ready",
)

SchedulerPolicyEvaluator = Callable[
    ["SchedulerPolicy", "dict[str, Any]"], "tuple[bool, str | None]"
]


@dataclass(frozen=True)
class SchedulerPolicy:
    """
    A named scheduling policy: unlike GovernancePolicy (scoped to one
    governance "operation"), a SchedulerPolicy applies universally to
    every job evaluate() is asked about — there is no operation to
    scope it to, since "should this job run right now" is the only
    question this engine ever answers.
    """

    name: str

    priority: int

    enabled: bool

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
            "conditions": dict(self.conditions),
        }


@dataclass(frozen=True)
class SchedulerPolicyDecision:
    """
    The immutable outcome of evaluating one job against every
    registered scheduler policy.
    """

    allowed: bool

    policy: "str | None"

    reason: "str | None"

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError(
                "evaluated_at must be timezone-aware"
            )

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
            "reason": self.reason,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class GovernanceSchedulerPolicyEngine:
    """
    Governs whether a scheduled job is allowed to run right now — when,
    where, and whether — distinct from GovernancePolicyEngine, which
    protects governance *operations* (lifecycle transitions, route
    changes) rather than scheduling decisions. The two are
    intentionally separate engines with separate registries: a
    maintenance-mode scheduling policy has nothing to do with who may
    restart the lifecycle manager.

    Evaluation is default-allow, exactly like GovernancePolicyEngine:
    a job is denied only if some enabled policy matches, in
    priority-then-name order, and the first match short-circuits the
    rest. A policy matches either via its own built-in or custom
    evaluator (register()'s policy_type/evaluator parameters), or —
    with neither given — by plain conditions-match against the
    context, the same fallback GovernancePolicyEngine itself uses.

    Every evaluate() call publishes an event, records into a wired
    GovernanceSchedulerMetrics, and records an audit entry via a wired
    GovernanceAuditService — all three optional, all three no-ops if
    not configured.

    Thread-safe: every mutation of the policy registry is guarded by
    an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._policies: "dict[str, SchedulerPolicy]" = {}

        self._evaluators: "dict[str, SchedulerPolicyEvaluator | None]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._metrics = metrics

        self._audit_service = audit_service

    def register(
        self,
        name: str,
        *,
        priority: int = 0,
        enabled: bool = True,
        conditions: "dict[str, Any] | None" = None,
        policy_type: "str | None" = None,
        evaluator: "SchedulerPolicyEvaluator | None" = None,
    ) -> SchedulerPolicy:
        """
        Register a new named policy.

        If evaluator is given, it is used directly (a custom policy).
        Otherwise, if policy_type names one of BUILT_IN_SCHEDULER_
        POLICIES, that built-in check is used. With neither given, the
        policy falls back to plain conditions-match against evaluate()'s
        context, like a GovernancePolicy with no rule attached.

        Raises ValueError if name is already registered, or if
        policy_type is given but not a recognized built-in.
        """

        with self._lock:
            if name in self._policies:
                raise ValueError(
                    f"policy '{name}' is already registered"
                )

            if evaluator is None and policy_type is not None:
                evaluator = self._built_in_evaluators().get(policy_type)

                if evaluator is None:
                    raise ValueError(
                        f"unknown built-in scheduler policy type "
                        f"'{policy_type}'"
                    )

            policy = SchedulerPolicy(
                name=name,
                priority=priority,
                enabled=enabled,
                conditions=conditions or {},
            )

            self._policies[name] = policy
            self._evaluators[name] = evaluator

        self._publish(
            "scheduler_policy_registered", name, policy.to_dict()
        )

        return policy

    def remove(self, name: str) -> None:
        """
        Remove a registered policy.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._policies:
                raise KeyError(f"policy '{name}' is not registered")

            del self._policies[name]
            self._evaluators.pop(name, None)

        self._publish("scheduler_policy_removed", name, {})

    def enable(self, name: str) -> SchedulerPolicy:
        """
        Enable a registered policy, returning its updated state.

        Raises KeyError if name is not registered. Idempotent.
        """

        return self._set_enabled(name, True)

    def disable(self, name: str) -> SchedulerPolicy:
        """
        Disable a registered policy, returning its updated state.

        Raises KeyError if name is not registered. Idempotent.
        """

        return self._set_enabled(name, False)

    def evaluate(
        self,
        job_id: str,
        context: "dict[str, Any] | None" = None,
    ) -> SchedulerPolicyDecision:
        """
        Evaluate job_id against every enabled policy, in priority-
        then-name order, returning a deny decision for the first
        policy that matches (disabled policies are skipped entirely)
        or an allow decision if none match.

        Publishes "scheduler_policy_allowed"/"scheduler_policy_denied",
        records into the wired metrics collector, and records an audit
        entry — all unconditionally, whichever way the decision goes.
        """

        context = context or {}

        for policy in self.list():
            if not policy.enabled:
                continue

            evaluator = self._evaluators.get(policy.name)

            if evaluator is not None:
                matched, reason = evaluator(policy, context)

            else:
                matched = conditions_match(policy.conditions, context)
                reason = (
                    f"job '{job_id}' matched policy '{policy.name}'"
                    if matched
                    else None
                )

            if matched:
                decision = SchedulerPolicyDecision(
                    allowed=False,
                    policy=policy.name,
                    reason=reason,
                    evaluated_at=self._clock(),
                )

                self._finalize_decision(job_id, decision)

                return decision

        decision = SchedulerPolicyDecision(
            allowed=True, policy=None, reason=None,
            evaluated_at=self._clock(),
        )

        self._finalize_decision(job_id, decision)

        return decision

    def evaluate_all(
        self,
        contexts: "dict[str, dict[str, Any]] | None" = None,
    ) -> "tuple[SchedulerPolicyDecision, ...]":
        """
        Evaluate every job_id in contexts (a job_id -> context
        mapping), in job_id order, returning one decision per job_id
        in that same deterministic order.
        """

        contexts = contexts or {}

        return tuple(
            self.evaluate(job_id, contexts[job_id])
            for job_id in sorted(contexts)
        )

    def list(self) -> "tuple[SchedulerPolicy, ...]":
        """
        Return every registered policy, ordered deterministically by
        priority then name.
        """

        with self._lock:
            policies = list(self._policies.values())

        return tuple(
            sorted(policies, key=lambda policy: (policy.priority, policy.name))
        )

    def clear(self) -> None:
        """
        Remove every registered policy.
        """

        with self._lock:
            self._policies.clear()
            self._evaluators.clear()

    def _finalize_decision(
        self, job_id: str, decision: SchedulerPolicyDecision
    ) -> None:
        event_type = (
            "scheduler_policy_allowed"
            if decision.allowed
            else "scheduler_policy_denied"
        )

        self._publish(event_type, job_id, decision.to_dict())

        if self._metrics is not None:
            self._metrics.record_policy_decision(allowed=decision.allowed)

        if self._audit_service is not None:
            self._audit_service.record(
                action=event_type,
                actor="system",
                resource=job_id,
                outcome="success" if decision.allowed else "failure",
                metadata=decision.to_dict(),
            )

    def _set_enabled(self, name: str, enabled: bool) -> SchedulerPolicy:
        with self._lock:
            policy = self._policies.get(name)

            if policy is None:
                raise KeyError(f"policy '{name}' is not registered")

            updated = replace(policy, enabled=enabled)
            self._policies[name] = updated

            return updated

    def _built_in_evaluators(self) -> "dict[str, SchedulerPolicyEvaluator]":
        return {
            "max_concurrent_jobs": self._evaluate_max_concurrent_jobs,
            "maintenance_mode": self._evaluate_maintenance_mode,
            "job_enabled": self._evaluate_job_enabled,
            "dependency_satisfied": self._evaluate_dependency_satisfied,
            "lock_acquired": self._evaluate_lock_acquired,
            "retry_limit_not_exceeded": (
                self._evaluate_retry_limit_not_exceeded
            ),
            "execution_window_allowed": (
                self._evaluate_execution_window_allowed
            ),
            "bootstrap_ready": self._evaluate_bootstrap_ready,
        }

    def _evaluate_max_concurrent_jobs(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        max_concurrent = policy.conditions.get("max_concurrent")

        if max_concurrent is None:
            return False, None

        active_jobs = context.get("active_jobs", 0)

        if active_jobs >= max_concurrent:
            return True, (
                f"active_jobs ({active_jobs}) >= max_concurrent "
                f"({max_concurrent})"
            )

        return False, None

    def _evaluate_maintenance_mode(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        if context.get("maintenance_mode", False):
            return True, "scheduler is in maintenance mode"

        return False, None

    def _evaluate_job_enabled(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        if not context.get("job_enabled", True):
            return True, "job is disabled"

        return False, None

    def _evaluate_dependency_satisfied(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        if not context.get("dependency_ready", True):
            return True, "job dependencies are not satisfied"

        return False, None

    def _evaluate_lock_acquired(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        if not context.get("lock_acquired", True):
            return True, "distributed lock could not be acquired"

        return False, None

    def _evaluate_retry_limit_not_exceeded(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        attempt = context.get("retry_attempt")
        max_attempts = context.get("max_attempts")

        if (
            attempt is not None
            and max_attempts is not None
            and attempt >= max_attempts
        ):
            return True, (
                f"retry attempt {attempt} has reached max_attempts "
                f"{max_attempts}"
            )

        return False, None

    def _evaluate_bootstrap_ready(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
    ) -> "tuple[bool, str | None]":
        """
        Deny while the scheduler bootstrap has not finished restoring
        persisted state and starting the scheduler yet — the concrete
        enforcement of the "restore persisted state before accepting
        work" rule GovernanceSchedulerBootstrap otherwise has no way
        to impose on run_due()'s own dispatch decisions.

        context["bootstrap_initialized"] defaults to True (allow) so
        a caller that never wires bootstrap status into its context
        sees identical behavior to before this policy type existed.
        """

        if not context.get("bootstrap_initialized", True):
            return True, "scheduler bootstrap has not completed yet"

        return False, None

    def _evaluate_execution_window_allowed(
        self, policy: SchedulerPolicy, context: "dict[str, Any]"
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
            # A window that wraps past midnight, e.g. 22 -> 6.
            within_window = hour >= start_hour or hour < end_hour

        if not within_window:
            return True, (
                f"current hour {hour} is outside the execution window "
                f"[{start_hour}, {end_hour})"
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


def build_default_governance_scheduler_policy_engine() -> (
    GovernanceSchedulerPolicyEngine
):
    """
    Build the process-wide governance scheduler policy engine, wired
    to the process-wide governance event bus, scheduler metrics
    collector, and audit service.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    return GovernanceSchedulerPolicyEngine(
        event_bus=get_event_bus(),
        metrics=get_scheduler_metrics(),
        audit_service=get_audit_service(),
    )


# Shared for the lifetime of the process: policies registered through
# the API need to be enforced by the same scheduler tick every other
# request or component sees, which a persistence runtime built fresh
# per request cannot provide on its own.
_scheduler_policy_engine = build_default_governance_scheduler_policy_engine()


def get_scheduler_policy_engine() -> GovernanceSchedulerPolicyEngine:
    """
    Return the process-wide governance scheduler policy engine.
    """

    return _scheduler_policy_engine
