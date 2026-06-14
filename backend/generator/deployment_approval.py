from dataclasses import dataclass


@dataclass
class DeploymentApproval:

    approved: bool

    decision: str

    approvers: list[str]

    rationale: list[str]


class DeploymentApprovalEngine:

    def evaluate(
        self,
        audit,
        risk
    ):

        rationale = []

        approved = (
            audit.compliant
            and
            risk.level != "high"
        )

        if audit.compliant:

            rationale.append(
                "Compliance checks passed"
            )

        if risk.level == "low":

            rationale.append(
                "Risk level acceptable"
            )

        if approved:

            decision = (
                "approved"
            )

        else:

            decision = (
                "blocked"
            )

        approvers = []

        if approved:

            approvers.append(
                "deployment-engine"
            )

        else:

            approvers.extend(
                [
                    "engineering",
                    "operations"
                ]
            )

        return DeploymentApproval(
            approved=
                approved,

            decision=
                decision,

            approvers=
                approvers,

            rationale=
                rationale
        )