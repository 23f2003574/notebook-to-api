from dataclasses import dataclass
from typing import List


@dataclass
class DeploymentPolicyRule:

    name: str

    condition: str

    action: str


@dataclass
class DeploymentPolicyDecision:

    service_name: str

    environment: str

    matched_rules: List[str]

    decision: str

    reasons: List[str]


class DeploymentPolicyEvaluationEngine:

    def evaluate(
        self,
        service_name: str,
        environment: str,
        risk_level: str,
        error_budget_exhausted: bool,
        burn_rate: float
    ):

        matched_rules = []

        reasons = []

        decision = "allow"

        normalized_environment = (
            environment
            .strip()
            .lower()
        )

        normalized_risk_level = (
            risk_level
            .strip()
            .lower()
        )

        if (
            normalized_environment == "production"
            and error_budget_exhausted
        ):

            matched_rules.append(
                "production_error_budget_policy"
            )

            reasons.append(
                "production release blocked because "
                "the service error budget is exhausted"
            )

            decision = "block"

        if (
            normalized_environment == "production"
            and burn_rate > 2.0
        ):

            matched_rules.append(
                "production_burn_rate_policy"
            )

            reasons.append(
                "production release blocked because "
                "the error budget burn rate exceeds policy"
            )

            decision = "block"

        if (
            decision != "block"
            and normalized_environment == "production"
            and normalized_risk_level == "critical"
        ):

            matched_rules.append(
                "critical_change_approval_policy"
            )

            reasons.append(
                "critical production change requires "
                "manual approval"
            )

            decision = "require_approval"

        if not matched_rules:

            reasons.append(
                "deployment satisfies all active policies"
            )

        return DeploymentPolicyDecision(

            service_name=
                service_name,

            environment=
                normalized_environment,

            matched_rules=
                matched_rules,

            decision=
                decision,

            reasons=
                reasons
        )
