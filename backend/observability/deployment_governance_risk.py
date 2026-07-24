from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_approval import DeploymentApprovalEngine
    from .deployment_governance_compliance import (
        DeploymentComplianceEngine,
    )
    from .deployment_governance_event_bus import GovernanceEventBus

# The score bands assess() classifies into — a fixed, non-overlapping
# partition of [0, 100]: LOW below 25, MEDIUM [25, 50), HIGH [50, 75),
# CRITICAL 75 and above.
RISK_LEVELS: "tuple[str, ...]" = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

_MEDIUM_THRESHOLD = 25.0
_HIGH_THRESHOLD = 50.0
_CRITICAL_THRESHOLD = 75.0

# The built-in risk factors this engine ships with, selectable by name
# via register_rule()'s factor parameter — the same plug-in shape
# BUILT_IN_ROLLOUT_POLICIES established, so a new risk factor never
# requires modifying this engine. Each reads specific, documented
# context keys rather than a conditions dict — RiskRule (unlike
# RolloutPolicy) carries no conditions field of its own to fall back
# on.
DEFAULT_RISK_FACTORS: "tuple[str, ...]" = (
    "production_deployment",
    "rollback_frequency",
    "failed_health_checks",
    "required_approvals_missing",
    "policy_violations",
)

# A rule's evaluator decides whether it is triggered for deployment_id
# given context — unlike ComplianceEvaluator/RolloutPolicyEvaluator,
# just a bare bool: RiskAssessment carries no per-rule reason, only an
# aggregate score. deployment_id is passed explicitly (not folded into
# context) so a built-in factor that needs it (required_approvals_
# missing, policy_violations) never depends on a caller having
# separately duplicated it into context.
RiskRuleEvaluator = Callable[
    ["RiskRule", str, "dict[str, Any]"], bool
]


def _level_for_score(score: float) -> str:
    if score >= _CRITICAL_THRESHOLD:
        return "CRITICAL"

    if score >= _HIGH_THRESHOLD:
        return "HIGH"

    if score >= _MEDIUM_THRESHOLD:
        return "MEDIUM"

    return "LOW"


@dataclass(frozen=True)
class RiskRule:
    """
    A named risk rule: how many points it contributes to a
    deployment's risk score (weight) if triggered, and whether it is
    currently in effect.
    """

    name: str

    weight: float

    enabled: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if self.weight < 0:
            raise ValueError("weight must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "weight": self.weight,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RiskAssessment:
    """
    The immutable outcome of scoring one deployment: its weighted
    score (0-100, see DeploymentRiskEngine.assess) and the RISK_LEVELS
    band that score falls into.
    """

    deployment_id: str

    score: float

    level: str

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not 0.0 <= self.score <= 100.0:
            raise ValueError("score must be between 0.0 and 100.0")

        if self.level not in RISK_LEVELS:
            raise ValueError(f"level must be one of {RISK_LEVELS}")

        if self.level != _level_for_score(self.score):
            raise ValueError(
                f"level '{self.level}' does not match score "
                f"{self.score}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "score": self.score,
            "level": self.level,
        }


@dataclass(frozen=True)
class RiskSummary:
    """
    An immutable, point-in-time snapshot of the risk rule registry
    itself — not an aggregate over past assessments. Approval
    enforcement, incident response, and reporting that consume actual
    assess() results are out of scope here, matching this commit's own
    stated scope.
    """

    total_rules: int

    enabled_rules: int

    disabled_rules: int

    total_weight: float

    def __post_init__(self) -> None:
        if self.total_rules < 0:
            raise ValueError("total_rules must not be negative")

        if self.enabled_rules + self.disabled_rules != self.total_rules:
            raise ValueError(
                "enabled_rules + disabled_rules must equal total_rules"
            )

        if self.total_weight < 0:
            raise ValueError("total_weight must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "total_rules": self.total_rules,
            "enabled_rules": self.enabled_rules,
            "disabled_rules": self.disabled_rules,
            "total_weight": self.total_weight,
        }


class DeploymentRiskEngine:
    """
    Scores deployment operations for risk before execution: every
    enabled rule that is triggered for a deployment (given context)
    contributes its weight to that deployment's score, capped at 100 —
    "weighted scoring (0-100)". Disabled rules are skipped entirely,
    contributing nothing.

    Ships with five default risk factors (DEFAULT_RISK_FACTORS),
    selectable via register_rule()'s factor parameter:
    "production_deployment" (context["environment"] == "production"),
    "rollback_frequency" (context["rollback_count"] >= 3),
    "failed_health_checks" (context["failed_health_checks"] > 0),
    "required_approvals_missing" (with an approval_engine wired in,
    DeploymentApprovalEngine.is_approved(deployment_id,
    context["operation"]) is False; otherwise
    context["required_approvals_missing"] truthy), and
    "policy_violations" (with a compliance_engine wired in,
    DeploymentComplianceEngine.violation_count(deployment_id, context)
    > 0; otherwise context["policy_violations"] > 0). Every default
    factor degrades to a context-only fallback when its optional
    engine is not wired — the same graceful-degradation contract every
    other optional-dependency integration in this codebase follows.

    Approval enforcement (blocking on a risk score), incident response,
    and reporting that consume these scores are out of scope here —
    this engine only computes and reports the score itself.

    Thread-safe: the rule registry and the last-assessment cache
    latest() reads are guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        compliance_engine: "DeploymentComplianceEngine | None" = None,
        approval_engine: "DeploymentApprovalEngine | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._rules: "dict[str, RiskRule]" = {}

        self._evaluators: "dict[str, RiskRuleEvaluator | None]" = {}

        self._latest: "dict[str, RiskAssessment]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._compliance_engine = compliance_engine

        self._approval_engine = approval_engine

    def register_rule(
        self,
        name: str,
        weight: float,
        *,
        enabled: bool = True,
        factor: "str | None" = None,
        evaluator: "RiskRuleEvaluator | None" = None,
    ) -> RiskRule:
        """
        Register a new named risk rule.

        If evaluator is given, it is used directly (a custom rule).
        Otherwise, if factor names one of DEFAULT_RISK_FACTORS, that
        built-in check is used. With neither given, the rule never
        triggers.

        Raises ValueError if name is already registered, or if factor
        is given but not a recognized built-in.
        """

        with self._lock:
            if name in self._rules:
                raise ValueError(
                    f"risk rule '{name}' is already registered"
                )

            if evaluator is None and factor is not None:
                evaluator = self._built_in_factors().get(factor)

                if evaluator is None:
                    raise ValueError(
                        f"unknown default risk factor '{factor}'"
                    )

            rule = RiskRule(name=name, weight=weight, enabled=enabled)

            self._rules[name] = rule
            self._evaluators[name] = evaluator

        return rule

    def remove_rule(self, name: str) -> None:
        """
        Remove a registered risk rule.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._rules:
                raise KeyError(f"risk rule '{name}' is not registered")

            del self._rules[name]
            self._evaluators.pop(name, None)

    def assess(
        self, deployment_id: str, context: "dict[str, Any] | None" = None
    ) -> RiskAssessment:
        """
        Score deployment_id: the sum of every enabled, triggered
        rule's weight (disabled rules ignored), capped at 100, banded
        into a RISK_LEVELS level.

        Raises ValueError if deployment_id is empty.
        """

        if not deployment_id:
            raise ValueError("deployment_id must not be empty")

        context = context or {}
        total = 0.0

        for rule in self.list():
            if not rule.enabled:
                continue

            evaluator = self._evaluators.get(rule.name)

            if evaluator is not None and evaluator(
                rule, deployment_id, context
            ):
                total += rule.weight

        score = min(100.0, max(0.0, total))
        level = _level_for_score(score)

        assessment = RiskAssessment(
            deployment_id=deployment_id, score=score, level=level,
        )

        with self._lock:
            self._latest[deployment_id] = assessment

        self._publish("risk_assessed", deployment_id, assessment.to_dict())

        if level == "HIGH":
            self._publish(
                "high_risk_detected", deployment_id, assessment.to_dict()
            )

        elif level == "CRITICAL":
            self._publish(
                "critical_risk_detected", deployment_id,
                assessment.to_dict(),
            )

        return assessment

    def assess_all(
        self,
        contexts: "dict[str, dict[str, Any]] | None" = None,
    ) -> "dict[str, RiskAssessment]":
        """
        Score every deployment_id in contexts (a deployment_id ->
        context mapping), in deployment_id order, returning a
        deployment_id -> its RiskAssessment mapping.
        """

        contexts = contexts or {}

        return {
            deployment_id: self.assess(
                deployment_id, contexts[deployment_id]
            )
            for deployment_id in sorted(contexts)
        }

    def latest(self, deployment_id: str) -> RiskAssessment:
        """
        Return deployment_id's most recently computed RiskAssessment.

        Raises KeyError if deployment_id has never been assess()ed.
        """

        with self._lock:
            assessment = self._latest.get(deployment_id)

            if assessment is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has not been "
                    "assessed"
                )

            return assessment

    def list(self) -> "tuple[RiskRule, ...]":
        """
        Return every registered risk rule, ordered deterministically
        by name.
        """

        with self._lock:
            rules = list(self._rules.values())

        return tuple(sorted(rules, key=lambda rule: rule.name))

    def summary(self) -> RiskSummary:
        """
        Return a point-in-time snapshot of the risk rule registry: how
        many rules are registered, how many are enabled/disabled, and
        their total enabled weight. Not an aggregate over past
        assess() results — see the class docstring.
        """

        rules = self.list()

        enabled_rules = [rule for rule in rules if rule.enabled]

        return RiskSummary(
            total_rules=len(rules),
            enabled_rules=len(enabled_rules),
            disabled_rules=len(rules) - len(enabled_rules),
            total_weight=sum(rule.weight for rule in enabled_rules),
        )

    def clear(self) -> None:
        """
        Remove every registered risk rule and every cached assessment.
        """

        with self._lock:
            self._rules.clear()
            self._evaluators.clear()
            self._latest.clear()

    def _built_in_factors(self) -> "dict[str, RiskRuleEvaluator]":
        return {
            "production_deployment": self._factor_production_deployment,
            "rollback_frequency": self._factor_rollback_frequency,
            "failed_health_checks": self._factor_failed_health_checks,
            "required_approvals_missing": (
                self._factor_required_approvals_missing
            ),
            "policy_violations": self._factor_policy_violations,
        }

    def _factor_production_deployment(
        self, rule: RiskRule, deployment_id: str, context: "dict[str, Any]"
    ) -> bool:
        return context.get("environment") == "production"

    def _factor_rollback_frequency(
        self, rule: RiskRule, deployment_id: str, context: "dict[str, Any]"
    ) -> bool:
        return context.get("rollback_count", 0) >= 3

    def _factor_failed_health_checks(
        self, rule: RiskRule, deployment_id: str, context: "dict[str, Any]"
    ) -> bool:
        return context.get("failed_health_checks", 0) > 0

    def _factor_required_approvals_missing(
        self, rule: RiskRule, deployment_id: str, context: "dict[str, Any]"
    ) -> bool:
        if self._approval_engine is not None and "operation" in context:
            return not self._approval_engine.is_approved(
                deployment_id, context["operation"]
            )

        return bool(context.get("required_approvals_missing", False))

    def _factor_policy_violations(
        self, rule: RiskRule, deployment_id: str, context: "dict[str, Any]"
    ) -> bool:
        if self._compliance_engine is not None:
            return (
                self._compliance_engine.violation_count(
                    deployment_id, context
                )
                > 0
            )

        return context.get("policy_violations", 0) > 0

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


def build_default_governance_risk_engine() -> DeploymentRiskEngine:
    """
    Build the process-wide deployment risk engine, wired to the
    process-wide governance event bus, compliance engine, and
    approval engine.
    """

    from .deployment_governance_approval import get_approval_engine
    from .deployment_governance_compliance import get_compliance_engine
    from .deployment_governance_event_bus import get_event_bus

    return DeploymentRiskEngine(
        event_bus=get_event_bus(),
        compliance_engine=get_compliance_engine(),
        approval_engine=get_approval_engine(),
    )


# Shared for the lifetime of the process: rules registered through the
# API need to be enforced identically by every caller, which a
# persistence runtime built fresh per request cannot provide on its
# own.
_risk_engine = build_default_governance_risk_engine()


def get_risk_engine() -> DeploymentRiskEngine:
    """
    Return the process-wide deployment risk engine.
    """

    return _risk_engine
