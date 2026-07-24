from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_event_bus import GovernanceEventBus

# The categories this engine ships knowledge of — documented
# vocabulary, not a closed set enforced by register() (any non-empty
# category string is accepted, matching how BUILT_IN_ROLLOUT_POLICIES
# documents a vocabulary without DeploymentRolloutPolicyEngine
# enforcing membership).
BUILT_IN_COMPLIANCE_CATEGORIES: "tuple[str, ...]" = (
    "Security",
    "Operations",
    "Change Management",
    "Data Protection",
    "Internal Policy",
)

# A policy's evaluator decides whether deployment_id (given context)
# complies, returning (compliant, reason): reason is set only when
# compliant is False. With none given at register() time, a policy
# trivially always passes — CompliancePolicy itself carries no
# conditions field to fall back on the way RolloutPolicy does, so
# "no evaluator" is the only default this engine can offer.
ComplianceEvaluator = Callable[
    ["CompliancePolicy", "dict[str, Any]"], "tuple[bool, str | None]"
]


@dataclass(frozen=True)
class CompliancePolicy:
    """
    A named compliance policy, scoped to a category
    (BUILT_IN_COMPLIANCE_CATEGORIES or a custom one) and independently
    enabled/disabled.
    """

    name: str

    category: str

    enabled: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if not self.category:
            raise ValueError("category must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "category": self.category,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ComplianceResult:
    """
    The immutable outcome of evaluating one policy against one
    deployment. reason is set if and only if compliant is False — the
    same allowed/denied invariant RolloutPolicyDecision enforces.
    """

    policy: str

    compliant: bool

    reason: "str | None"

    def __post_init__(self) -> None:
        if not self.policy:
            raise ValueError("policy must not be empty")

        if self.compliant:
            if self.reason is not None:
                raise ValueError(
                    "reason must not be set when compliant is True"
                )

        else:
            if self.reason is None:
                raise ValueError(
                    "reason must be set when compliant is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "compliant": self.compliant,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ComplianceSummary:
    """
    An immutable, point-in-time snapshot of the compliance policy
    registry itself — not a per-deployment evaluation report (that is
    the deferred "reporting" concern this commit deliberately leaves
    for later); just how many policies are registered, how many are
    enabled/disabled, and their breakdown by category.
    """

    total_policies: int

    enabled_policies: int

    disabled_policies: int

    categories: "dict[str, int]"

    def __post_init__(self) -> None:
        if self.total_policies < 0:
            raise ValueError("total_policies must not be negative")

        if self.enabled_policies + self.disabled_policies != (
            self.total_policies
        ):
            raise ValueError(
                "enabled_policies + disabled_policies must equal "
                "total_policies"
            )

        object.__setattr__(
            self, "categories", MappingProxyType(dict(self.categories))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_policies": self.total_policies,
            "enabled_policies": self.enabled_policies,
            "disabled_policies": self.disabled_policies,
            "categories": dict(self.categories),
        }


class DeploymentComplianceEngine:
    """
    Evaluates deployments against registered compliance policies —
    regulatory and organizational rules scoped to a category
    (BUILT_IN_COMPLIANCE_CATEGORIES: Security, Operations, Change
    Management, Data Protection, Internal Policy, or a custom one).

    Unlike DeploymentRolloutPolicyEngine.evaluate() (a single allow/
    deny decision from the first matching policy), evaluate() here
    checks a deployment against *every* enabled policy and reports one
    ComplianceResult per policy — a compliance check is an audit of
    everything that applies, not a single gate. Disabled policies are
    skipped entirely, contributing no ComplianceResult.

    Compliance reporting, dashboards, and enforcement (blocking a
    rollout or an approval on a failed policy) are out of scope here
    — this engine only evaluates and reports; consulting these results
    from anywhere else in this codebase's runtime is left to a later
    commit.

    Thread-safe: every mutation of the policy registry is guarded by
    an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._policies: "dict[str, CompliancePolicy]" = {}

        self._evaluators: "dict[str, ComplianceEvaluator | None]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._audit_service = audit_service

    def register(
        self,
        name: str,
        category: str,
        *,
        enabled: bool = True,
        evaluator: "ComplianceEvaluator | None" = None,
    ) -> CompliancePolicy:
        """
        Register a new named compliance policy. With no evaluator
        given, the policy trivially always reports compliant.

        Raises ValueError if name is already registered — "unique
        policy names".
        """

        with self._lock:
            if name in self._policies:
                raise ValueError(
                    f"compliance policy '{name}' is already registered"
                )

            policy = CompliancePolicy(
                name=name, category=category, enabled=enabled
            )

            self._policies[name] = policy
            self._evaluators[name] = evaluator

        self._publish("policy_registered", name, policy.to_dict())

        self._record_audit(
            action="policy_registered", actor="system", resource=name,
            outcome="success", metadata=policy.to_dict(),
        )

        return policy

    def remove(self, name: str) -> None:
        """
        Remove a registered compliance policy.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._policies:
                raise KeyError(
                    f"compliance policy '{name}' is not registered"
                )

            del self._policies[name]
            self._evaluators.pop(name, None)

        self._publish("policy_removed", name, {})

        self._record_audit(
            action="policy_removed", actor="system", resource=name,
            outcome="success",
        )

    def evaluate(
        self, deployment_id: str, context: "dict[str, Any] | None" = None
    ) -> "tuple[ComplianceResult, ...]":
        """
        Evaluate deployment_id against every enabled compliance
        policy (disabled policies skipped — "disabled policies
        skipped"), in name order, returning one ComplianceResult per
        enabled policy — not a single first-match decision.

        Raises ValueError if deployment_id is empty.
        """

        if not deployment_id:
            raise ValueError("deployment_id must not be empty")

        context = context or {}
        results = []

        for policy in self.list():
            if not policy.enabled:
                continue

            evaluator = self._evaluators.get(policy.name)

            if evaluator is not None:
                compliant, reason = evaluator(policy, context)

            else:
                compliant, reason = True, None

            result = ComplianceResult(
                policy=policy.name, compliant=compliant, reason=reason,
            )

            results.append(result)

            self._finalize_result(deployment_id, result)

        return tuple(results)

    def evaluate_all(
        self,
        contexts: "dict[str, dict[str, Any]] | None" = None,
    ) -> "dict[str, tuple[ComplianceResult, ...]]":
        """
        Evaluate every deployment_id in contexts (a deployment_id ->
        context mapping) against every enabled compliance policy, in
        deployment_id order, returning a deployment_id -> its
        ComplianceResult tuple mapping.
        """

        contexts = contexts or {}

        return {
            deployment_id: self.evaluate(
                deployment_id, contexts[deployment_id]
            )
            for deployment_id in sorted(contexts)
        }

    def list(self) -> "tuple[CompliancePolicy, ...]":
        """
        Return every registered compliance policy, ordered
        deterministically by name.
        """

        with self._lock:
            policies = list(self._policies.values())

        return tuple(
            sorted(policies, key=lambda policy: policy.name)
        )

    def summary(self) -> ComplianceSummary:
        """
        Return a point-in-time snapshot of the compliance policy
        registry: how many policies are registered, how many are
        enabled/disabled, and their breakdown by category. Not a
        per-deployment evaluation report — see the class docstring.
        """

        policies = self.list()

        enabled = sum(1 for policy in policies if policy.enabled)

        categories: "dict[str, int]" = {}

        for policy in policies:
            categories[policy.category] = (
                categories.get(policy.category, 0) + 1
            )

        return ComplianceSummary(
            total_policies=len(policies),
            enabled_policies=enabled,
            disabled_policies=len(policies) - enabled,
            categories=categories,
        )

    def clear(self) -> None:
        """
        Remove every registered compliance policy.
        """

        with self._lock:
            self._policies.clear()
            self._evaluators.clear()

    def _finalize_result(
        self, deployment_id: str, result: ComplianceResult
    ) -> None:
        event_type = (
            "compliance_passed" if result.compliant else "compliance_failed"
        )

        self._publish(event_type, deployment_id, result.to_dict())

        self._record_audit(
            action=event_type, actor="system",
            resource=f"{deployment_id}:{result.policy}",
            outcome="success" if result.compliant else "failure",
            metadata=result.to_dict(),
        )

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

    def _record_audit(
        self,
        *,
        action: str,
        actor: str,
        resource: str,
        outcome: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> None:
        if self._audit_service is None:
            return

        self._audit_service.record(
            action=action, actor=actor, resource=resource,
            outcome=outcome, metadata=metadata or {},
        )


def build_default_governance_compliance_engine() -> (
    DeploymentComplianceEngine
):
    """
    Build the process-wide deployment compliance engine, wired to the
    process-wide governance event bus and audit service.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus

    return DeploymentComplianceEngine(
        event_bus=get_event_bus(), audit_service=get_audit_service(),
    )


# Shared for the lifetime of the process: policies registered through
# the API need to be enforced identically by every caller, which a
# persistence runtime built fresh per request cannot provide on its
# own.
_compliance_engine = build_default_governance_compliance_engine()


def get_compliance_engine() -> DeploymentComplianceEngine:
    """
    Return the process-wide deployment compliance engine.
    """

    return _compliance_engine
