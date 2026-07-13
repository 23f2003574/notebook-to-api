from dataclasses import dataclass
from typing import List


@dataclass
class DeploymentExecutionEligibilityDecision:

    service_name: str

    environment: str

    eligible: bool

    decision: str

    reasons: List[str]


@dataclass
class DeploymentExecutionReadiness:

    policy_decision: object

    approval_validity: object

    eligibility: (
        DeploymentExecutionEligibilityDecision
    )


class DeploymentExecutionEligibilityEngine:

    def evaluate(
        self,
        service_name: str,
        environment: str,
        policy_decision: str,
        approval_required: bool,
        approval_valid: bool,
        error_budget_exhausted: bool,
        burn_rate: float,
        active_incidents: int
    ):

        reasons = []

        normalized_environment = (
            environment
            .strip()
            .lower()
        )

        normalized_policy_decision = (
            policy_decision
            .strip()
            .lower()
        )

        if normalized_policy_decision == "block":

            reasons.append(
                "current deployment policy blocks execution"
            )

        if (
            approval_required
            and not approval_valid
        ):

            reasons.append(
                "deployment requires a valid approval"
            )

        if error_budget_exhausted:

            reasons.append(
                "service error budget is exhausted"
            )

        if burn_rate > 2.0:

            reasons.append(
                "service error budget burn rate "
                "exceeds the execution threshold"
            )

        if active_incidents > 0:

            reasons.append(
                "service has active incidents"
            )

        eligible = (
            normalized_policy_decision != "block"
            and (
                not approval_required
                or approval_valid
            )
            and not error_budget_exhausted
            and burn_rate <= 2.0
            and active_incidents == 0
        )

        decision = (
            "execute"
            if eligible
            else "deny"
        )

        if eligible:

            reasons.append(
                "deployment satisfies all "
                "execution eligibility requirements"
            )

        return DeploymentExecutionEligibilityDecision(

            service_name=
                service_name,

            environment=
                normalized_environment,

            eligible=
                eligible,

            decision=
                decision,

            reasons=
                reasons
        )
