from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_rules import (
    GovernanceRuleEngine,
    conditions_match,
    get_rule_engine,
)

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService

WILDCARD_OPERATION = "*"


@dataclass(frozen=True)
class GovernancePolicy:
    """
    A named policy rule: an operation is denied if this policy
    matches it, either because a named rule (evaluated through the
    rule engine) fails, or — with no rule attached — because
    conditions all match the given context.

    There is no separate "allow" policy: the engine is default-allow,
    and a policy exists only to carve out a deny rule against that
    default. conditions being empty (with no rule attached) means the
    policy denies every context for its operation unconditionally.
    """

    name: str

    operation: str

    priority: int

    enabled: bool = True

    conditions: "dict[str, Any]" = dataclasses.field(default_factory=dict)

    rule: "str | None" = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if not self.operation:
            raise ValueError("operation must not be empty")

        object.__setattr__(
            self, "conditions", MappingProxyType(dict(self.conditions))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "operation": self.operation,
            "priority": self.priority,
            "enabled": self.enabled,
            "conditions": dict(self.conditions),
            "rule": self.rule,
        }


@dataclass(frozen=True)
class PolicyDecision:
    """
    The immutable outcome of evaluating one operation against every
    registered policy.
    """

    allowed: bool

    policy: "str | None"

    reason: "str | None"

    evaluated_at: datetime

    def __post_init__(self) -> None:
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
            "reason": self.reason,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class GovernancePolicyViolation(RuntimeError):
    """
    Raised when a governance operation is denied by policy, aborting
    the operation before it takes effect.
    """

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision

        message = "operation denied by policy"

        if decision.policy is not None:
            message += f" '{decision.policy}'"

        if decision.reason is not None:
            message += f": {decision.reason}"

        super().__init__(message)


class GovernancePolicyEngine:
    """
    A single enforcement point for governance operations: callers ask
    "is this operation, with this context, currently allowed?" before
    acting, rather than each component independently deciding what is
    and is not permitted.

    Evaluation is default-allow: an operation is denied only if some
    enabled policy registered for it (or for the "*" wildcard
    operation) matches the given context. The first such policy, in
    priority-then-name order, wins and stops evaluation there.

    A policy matches in one of two ways: if it has a rule attached
    and this engine was constructed with a rule_engine, it matches
    when that named rule fails (policies consume rule results rather
    than implementing their own evaluation logic); otherwise it falls
    back to conditions — all matching the context — the same inline
    check this engine used before the rule engine existed.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        rule_engine: "GovernanceRuleEngine | None" = None,
    ) -> None:
        self._policies: "dict[str, GovernancePolicy]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._rule_engine = rule_engine

    def register(
        self,
        name: str,
        *,
        operation: str,
        priority: int = 0,
        enabled: bool = True,
        conditions: "dict[str, Any] | None" = None,
        rule: "str | None" = None,
    ) -> GovernancePolicy:
        """
        Register a new named policy.

        Raises ValueError if name is already registered.
        """

        if name in self._policies:
            raise ValueError(f"policy '{name}' is already registered")

        policy = GovernancePolicy(
            name=name,
            operation=operation,
            priority=priority,
            enabled=enabled,
            conditions=conditions or {},
            rule=rule,
        )

        self._policies[name] = policy

        return policy

    def remove(self, name: str) -> None:
        """
        Remove a registered policy.

        Raises KeyError if name is not registered.
        """

        if name not in self._policies:
            raise KeyError(f"policy '{name}' is not registered")

        del self._policies[name]

    def enable(self, name: str) -> GovernancePolicy:
        """
        Enable a registered policy, returning its updated state.

        Raises KeyError if name is not registered. Idempotent.
        """

        return self._set_enabled(name, True)

    def disable(self, name: str) -> GovernancePolicy:
        """
        Disable a registered policy, returning its updated state.

        Raises KeyError if name is not registered. Idempotent.
        """

        return self._set_enabled(name, False)

    def list(self) -> "tuple[GovernancePolicy, ...]":
        """
        Return every registered policy, ordered deterministically by
        priority then name.
        """

        return tuple(
            sorted(
                self._policies.values(),
                key=lambda policy: (policy.priority, policy.name),
            )
        )

    def evaluate(
        self,
        operation: str,
        context: "dict[str, Any] | None" = None,
    ) -> PolicyDecision:
        """
        Evaluate operation against every enabled policy registered
        for it (or for the "*" wildcard operation), in priority-then-
        name order.

        Returns a deny decision for the first policy that matches,
        without evaluating any further policy. Returns an allow
        decision if none match (including when no policies are
        registered at all: this engine is default-allow).
        """

        context = context or {}

        for policy in self.list():
            if not policy.enabled:
                continue

            if (
                policy.operation != WILDCARD_OPERATION
                and policy.operation != operation
            ):
                continue

            matched, reason = self._policy_matches(
                policy, operation, context
            )

            if matched:
                return PolicyDecision(
                    allowed=False,
                    policy=policy.name,
                    reason=reason,
                    evaluated_at=self._clock(),
                )

        return PolicyDecision(
            allowed=True,
            policy=None,
            reason=None,
            evaluated_at=self._clock(),
        )

    def clear(self) -> None:
        """
        Remove every registered policy.
        """

        self._policies.clear()

    def authorize(
        self,
        operation: str,
        context: "dict[str, Any] | None" = None,
        *,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> PolicyDecision:
        """
        Evaluate operation and raise GovernancePolicyViolation if
        denied; otherwise return the (allowing) decision.

        Optionally records the decision through audit_service either
        way. This is the evaluate-record-enforce sequence every
        policy-protected operation in this codebase already repeats
        (the lifecycle manager, the event router, the audit
        service's own purge()); new callers — the recovery manager,
        and any future one — can use this instead of duplicating that
        sequence again themselves.
        """

        decision = self.evaluate(operation, context)

        if audit_service is not None:
            from .deployment_governance_audit import (
                record_policy_decision,
            )

            record_policy_decision(audit_service, operation, decision)

        if not decision.allowed:
            raise GovernancePolicyViolation(decision)

        return decision

    def _policy_matches(
        self,
        policy: GovernancePolicy,
        operation: str,
        context: "dict[str, Any]",
    ) -> "tuple[bool, str | None]":
        if policy.rule is not None and self._rule_engine is not None:
            result = self._rule_engine.evaluate(policy.rule, context)

            if result.passed:
                return False, None

            return True, (
                f"operation '{operation}' matched policy "
                f"'{policy.name}': rule '{policy.rule}' failed"
                + (f" ({result.reason})" if result.reason else "")
            )

        if conditions_match(policy.conditions, context):
            return True, (
                f"operation '{operation}' matched policy "
                f"'{policy.name}'"
            )

        return False, None

    def _set_enabled(self, name: str, enabled: bool) -> GovernancePolicy:
        policy = self._policies.get(name)

        if policy is None:
            raise KeyError(f"policy '{name}' is not registered")

        updated = dataclasses.replace(policy, enabled=enabled)

        self._policies[name] = updated

        return updated


# Shared for the lifetime of the process: policies registered through
# the API need to be enforced by whichever component (lifecycle
# manager, event router, audit service) evaluates against them. Wired
# to the process-wide rule engine (deployment_governance_rules has no
# dependency on this module, so a plain top-level import is safe —
# no circularity to avoid) so a policy can consume a named rule's
# result instead of implementing its own evaluation logic.
_policy_engine = GovernancePolicyEngine(rule_engine=get_rule_engine())


def get_policy_engine() -> GovernancePolicyEngine:
    """
    Return the process-wide governance policy engine.
    """

    return _policy_engine
