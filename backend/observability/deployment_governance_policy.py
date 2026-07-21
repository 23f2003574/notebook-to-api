from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable

WILDCARD_OPERATION = "*"


@dataclass(frozen=True)
class GovernancePolicy:
    """
    A named policy rule: if an operation's conditions all match the
    context an operation is evaluated with, that operation is denied.

    There is no separate "allow" policy: the engine is default-allow,
    and a policy exists only to carve out a deny rule against that
    default. conditions being empty means the policy denies every
    context for its operation unconditionally.
    """

    name: str

    operation: str

    priority: int

    enabled: bool = True

    conditions: "dict[str, Any]" = dataclasses.field(default_factory=dict)

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
    operation) has conditions that all match the given context. The
    first such policy, in priority-then-name order, wins and stops
    evaluation there.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
    ) -> None:
        self._policies: "dict[str, GovernancePolicy]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def register(
        self,
        name: str,
        *,
        operation: str,
        priority: int = 0,
        enabled: bool = True,
        conditions: "dict[str, Any] | None" = None,
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

        Returns a deny decision for the first policy whose conditions
        all match context, without evaluating any further policy.
        Returns an allow decision if none match (including when no
        policies are registered at all: this engine is default-allow).
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

            if self._conditions_match(policy.conditions, context):
                return PolicyDecision(
                    allowed=False,
                    policy=policy.name,
                    reason=(
                        f"operation '{operation}' matched policy "
                        f"'{policy.name}'"
                    ),
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

    @staticmethod
    def _conditions_match(
        conditions: "dict[str, Any]", context: "dict[str, Any]"
    ) -> bool:
        return all(
            context.get(key) == value
            for key, value in conditions.items()
        )

    def _set_enabled(self, name: str, enabled: bool) -> GovernancePolicy:
        policy = self._policies.get(name)

        if policy is None:
            raise KeyError(f"policy '{name}' is not registered")

        updated = dataclasses.replace(policy, enabled=enabled)

        self._policies[name] = updated

        return updated


# Shared for the lifetime of the process: policies registered through
# the API need to be enforced by whichever component (lifecycle
# manager, event router, audit service) evaluates against them.
_policy_engine = GovernancePolicyEngine()


def get_policy_engine() -> GovernancePolicyEngine:
    """
    Return the process-wide governance policy engine.
    """

    return _policy_engine
