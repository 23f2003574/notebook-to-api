from dataclasses import dataclass


@dataclass
class CostRisk:

    risk_name: str

    probability: str

    financial_impact: str


class CostRiskAnalysisEngine:

    def generate(
        self
    ):

        return [

            CostRisk(

                risk_name=
                    "traffic_growth",

                probability=
                    "high",

                financial_impact=
                    "high"
            ),

            CostRisk(

                risk_name=
                    "resource_overprovisioning",

                probability=
                    "medium",

                financial_impact=
                    "medium"
            ),

            CostRisk(

                risk_name=
                    "third_party_cost_increase",

                probability=
                    "medium",

                financial_impact=
                    "high"
            )
        ]
