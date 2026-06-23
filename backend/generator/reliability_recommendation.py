from dataclasses import dataclass


@dataclass
class ReliabilityRecommendation:

    recommendation: str

    impact: str

    priority: str


class ReliabilityRecommendationEngine:

    def generate(
        self
    ):

        return [

            ReliabilityRecommendation(

                recommendation=
                    "add_request_retries",

                impact=
                    "high",

                priority=
                    "high"
            ),

            ReliabilityRecommendation(

                recommendation=
                    "increase_health_checks",

                impact=
                    "medium",

                priority=
                    "medium"
            ),

            ReliabilityRecommendation(

                recommendation=
                    "add_failover_strategy",

                impact=
                    "high",

                priority=
                    "high"
            )
        ]