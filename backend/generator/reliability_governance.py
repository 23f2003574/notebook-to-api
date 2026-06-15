from dataclasses import dataclass


@dataclass
class ReliabilityGovernance:

    compliant: bool

    policy_status: str

    required_actions: list[str]

    decision: str


class ReliabilityGovernanceEngine:

    def evaluate(
        self,
        scorecard
    ):

        required_actions = []

        if (
            scorecard.grade
            in ["D"]
        ):

            required_actions.extend(
                [
                    "Immediate reliability review",
                    "Block production deployment"
                ]
            )

            return (
                ReliabilityGovernance(

                    compliant=False,

                    policy_status=
                        "violation",

                    required_actions=
                        required_actions,

                    decision=
                        "blocked"
                )
            )

        if (
            scorecard.grade
            == "C"
        ):

            required_actions.append(
                "Reliability improvement plan"
            )

            return (
                ReliabilityGovernance(

                    compliant=False,

                    policy_status=
                        "warning",

                    required_actions=
                        required_actions,

                    decision=
                        "review-required"
                )
            )

        return (
            ReliabilityGovernance(

                compliant=True,

                policy_status=
                    "compliant",

                required_actions=[],

                decision=
                    "approved"
            )
        )