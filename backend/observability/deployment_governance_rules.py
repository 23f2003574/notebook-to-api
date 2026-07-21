from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Union

from .deployment_governance_health import evaluate_component_check

WILDCARD_OPERATION = "*"

RuleCheckResult = Union[bool, "tuple[bool, str | None]"]


@dataclass(frozen=True)
class GovernanceRule:
    """
    A named, registered rule's metadata. The predicate it actually
    evaluates is supplied separately to
    GovernanceRuleEngine.register() rather than stored on this
    dataclass, the same way a lifecycle component's start/stop
    callables live in the manager rather than on LifecycleComponent.
    """

    name: str

    operation: str

    priority: int

    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if not self.operation:
            raise ValueError("operation must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "operation": self.operation,
            "priority": self.priority,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RuleEvaluationResult:
    """
    The immutable outcome of evaluating one rule.
    """

    rule: str

    passed: bool

    reason: "str | None"

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")

        if self.passed and self.reason is not None:
            raise ValueError(
                "reason must not be set when passed is True"
            )

        if not self.passed and self.reason is None:
            raise ValueError(
                "reason must be set when passed is False"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "passed": self.passed,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass
class _RegisteredRule:
    definition: GovernanceRule
    check: "Callable[[dict[str, Any]], RuleCheckResult]"


class GovernanceRuleEngine:
    """
    A reusable, composable predicate engine: a rule is a named,
    priority-ordered, independently testable check, decoupled from
    whatever governance module ends up consuming its result.

    GovernancePolicyEngine is the primary consumer today (a policy
    may reference a rule by name instead of implementing its own
    condition-matching logic), but nothing here is policy-specific:
    any governance module can register and evaluate rules of its own.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
    ) -> None:
        self._rules: "dict[str, _RegisteredRule]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def register(
        self,
        name: str,
        *,
        operation: str = WILDCARD_OPERATION,
        priority: int = 0,
        enabled: bool = True,
        check: "Callable[[dict[str, Any]], RuleCheckResult]",
    ) -> GovernanceRule:
        """
        Register a new named rule and the predicate it evaluates.

        check receives the evaluation context dict (empty if none was
        given) and returns a bool, or a (bool, reason) tuple.

        Raises ValueError if name is already registered.
        """

        if name in self._rules:
            raise ValueError(f"rule '{name}' is already registered")

        definition = GovernanceRule(
            name=name,
            operation=operation,
            priority=priority,
            enabled=enabled,
        )

        self._rules[name] = _RegisteredRule(
            definition=definition, check=check
        )

        return definition

    def remove(self, name: str) -> None:
        """
        Remove a registered rule.

        Raises KeyError if name is not registered.
        """

        if name not in self._rules:
            raise KeyError(f"rule '{name}' is not registered")

        del self._rules[name]

    def enable(self, name: str) -> GovernanceRule:
        """
        Enable a registered rule, returning its updated definition.

        Raises KeyError if name is not registered. Idempotent.
        """

        return self._set_enabled(name, True)

    def disable(self, name: str) -> GovernanceRule:
        """
        Disable a registered rule, returning its updated definition.

        Raises KeyError if name is not registered. Idempotent.
        """

        return self._set_enabled(name, False)

    def evaluate(
        self,
        name: str,
        context: "dict[str, Any] | None" = None,
    ) -> RuleEvaluationResult:
        """
        Evaluate the named rule directly, regardless of its
        operation, against context.

        A disabled rule still produces a decision (failed, "rule is
        disabled") rather than raising, since every evaluation
        produces a decision object — the "disabled rules skipped"
        rule applies to evaluate_all()'s batch view, not to an
        explicit, direct request for one named rule.

        An exception raised by the rule's check is converted to a
        failed evaluation rather than propagating.

        Raises LookupError if name is not registered.
        """

        entry = self._rules.get(name)

        if entry is None:
            raise LookupError(f"no rule registered with name '{name}'")

        if not entry.definition.enabled:
            return RuleEvaluationResult(
                rule=name,
                passed=False,
                reason="rule is disabled",
                evaluated_at=self._clock(),
            )

        context = context or {}

        passed, reason = evaluate_component_check(
            name,
            lambda: entry.check(context),
            default_message=f"rule '{name}' did not pass",
        )

        return RuleEvaluationResult(
            rule=name,
            passed=passed,
            reason=reason,
            evaluated_at=self._clock(),
        )

    def evaluate_all(
        self,
        operation: str,
        context: "dict[str, Any] | None" = None,
    ) -> "tuple[RuleEvaluationResult, ...]":
        """
        Evaluate every enabled rule registered for operation (or for
        the "*" wildcard operation), in priority-then-name order.
        Disabled rules are skipped entirely rather than represented
        with a "disabled" result.
        """

        results = []

        for definition in self.list():
            if not definition.enabled:
                continue

            if (
                definition.operation != WILDCARD_OPERATION
                and definition.operation != operation
            ):
                continue

            results.append(self.evaluate(definition.name, context))

        return tuple(results)

    def list(self) -> "tuple[GovernanceRule, ...]":
        """
        Return every registered rule's definition, ordered
        deterministically by priority then name.
        """

        return tuple(
            sorted(
                (entry.definition for entry in self._rules.values()),
                key=lambda definition: (
                    definition.priority,
                    definition.name,
                ),
            )
        )

    def clear(self) -> None:
        """
        Remove every registered rule.
        """

        self._rules.clear()

    def _set_enabled(self, name: str, enabled: bool) -> GovernanceRule:
        entry = self._rules.get(name)

        if entry is None:
            raise KeyError(f"rule '{name}' is not registered")

        updated = dataclasses.replace(entry.definition, enabled=enabled)

        self._rules[name] = _RegisteredRule(
            definition=updated, check=entry.check
        )

        return updated


def conditions_match(
    conditions: "dict[str, Any]", context: "dict[str, Any]"
) -> bool:
    """
    Return whether every key/value in conditions is present and equal
    in context.

    This is the "does a set of exact-match conditions apply to this
    context" predicate GovernancePolicyEngine used to implement
    inline; it now lives here so it is independently testable and
    reusable outside of policy evaluation, the same way
    GovernanceEventRouter's route_matches() is shared with
    GovernanceEventHistory.
    """

    return all(
        context.get(key) == value for key, value in conditions.items()
    )


def build_default_governance_rule_engine() -> GovernanceRuleEngine:
    """
    Build the governance rule engine's built-in rule set: general
    system-state checks any governance module can consult, evaluated
    against the process-wide singletons they each read from.
    """

    engine = GovernanceRuleEngine()

    engine.register(
        "runtime_initialized",
        priority=10,
        check=_check_runtime_initialized,
    )

    engine.register(
        "component_healthy",
        priority=20,
        check=_check_component_healthy,
    )

    engine.register(
        "provider_registered",
        priority=30,
        check=_check_provider_registered,
    )

    engine.register(
        "dependency_satisfied",
        priority=40,
        check=_check_dependency_satisfied,
    )

    engine.register(
        "lifecycle_idle",
        priority=50,
        check=_check_lifecycle_idle,
    )

    engine.register(
        "audit_chain_valid",
        priority=60,
        check=_check_audit_chain_valid,
    )

    engine.register(
        "event_history_available",
        priority=70,
        check=_check_event_history_available,
    )

    return engine


def _check_runtime_initialized(context: "dict[str, Any]") -> RuleCheckResult:
    from .deployment_governance_lifecycle import get_lifecycle_manager

    if not get_lifecycle_manager().status():
        return False, "no governance components are registered"

    return True


def _check_component_healthy(context: "dict[str, Any]") -> RuleCheckResult:
    from .deployment_governance_lifecycle import get_lifecycle_manager

    not_started = [
        component.name
        for component in get_lifecycle_manager().status()
        if not component.started
    ]

    if not_started:
        return False, "not started: " + ", ".join(sorted(not_started))

    return True


def _check_provider_registered(context: "dict[str, Any]") -> RuleCheckResult:
    from .deployment_governance_persistence import (
        build_deployment_governance_persistence,
    )
    from .deployment_governance_readiness import count_registered_providers

    registry = (
        build_deployment_governance_persistence()
        .build_integrity_provider_registry()
    )

    count = count_registered_providers(registry)

    if not count:
        return False, "no providers are registered"

    return True


def _check_dependency_satisfied(context: "dict[str, Any]") -> RuleCheckResult:
    from .deployment_governance_bootstrap import (
        build_governance_dependency_graph,
    )

    result = build_governance_dependency_graph().validate()

    if not result.valid:
        return False, "governance dependency graph is invalid"

    return True


def _check_lifecycle_idle(context: "dict[str, Any]") -> RuleCheckResult:
    from .deployment_governance_lifecycle import get_lifecycle_manager

    started = [
        component.name
        for component in get_lifecycle_manager().status()
        if component.started
    ]

    if started:
        return False, "started: " + ", ".join(sorted(started))

    return True


def _check_audit_chain_valid(context: "dict[str, Any]") -> RuleCheckResult:
    from .deployment_governance_audit import get_audit_service

    result = get_audit_service().verify_chain()

    if not result.valid:
        return False, result.reason

    return True


def _check_event_history_available(
    context: "dict[str, Any]",
) -> RuleCheckResult:
    from .deployment_governance_event_history import get_event_history

    get_event_history().size()

    return True


# Shared for the lifetime of the process: rules registered through
# the API need to be visible to whatever evaluates them (the policy
# engine, or a direct API caller).
_rule_engine = build_default_governance_rule_engine()


def get_rule_engine() -> GovernanceRuleEngine:
    """
    Return the process-wide governance rule engine.
    """

    return _rule_engine
