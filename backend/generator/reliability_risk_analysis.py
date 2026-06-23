from dataclasses import dataclass


@dataclass
class ReliabilityRisk:

    risk_name: str

    probability: str

    impact: str


class ReliabilityRiskAnalysisEngine:

    def generate(
        self
    ):

        return [

            ReliabilityRisk(

                risk_name=
                    "single_point_of_failure",

                probability=
                    "medium",

                impact=
                    "high"
            ),

            ReliabilityRisk(

                risk_name=
                    "dependency_outage",

                probability=
                    "high",

                impact=
                    "high"
            ),

            ReliabilityRisk(

                risk_name=
                    "traffic_spike",

                probability=
                    "medium",

                impact=
                    "critical"
            )
        ]
