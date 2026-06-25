from dataclasses import dataclass


@dataclass
class GovernanceRisk:

    risk_name: str

    probability: str

    impact: str


class GovernanceRiskAnalysisEngine:

    def generate(
        self
    ):

        return [

            GovernanceRisk(

                risk_name=
                    "incomplete_audit_logging",

                probability=
                    "medium",

                impact=
                    "high"
            ),

            GovernanceRisk(

                risk_name=
                    "compliance_gap",

                probability=
                    "medium",

                impact=
                    "high"
            ),

            GovernanceRisk(

                risk_name=
                    "policy_drift",

                probability=
                    "low",

                impact=
                    "medium"
            )
        ]
