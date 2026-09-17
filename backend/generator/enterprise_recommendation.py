from dataclasses import dataclass


@dataclass
class EnterpriseRecommendation:

    recommendation: str

    category: str

    priority: str


class EnterpriseRecommendationEngine:

    def generate(
        self
    ):

        return [
            EnterpriseRecommendation(
                recommendation=
                    "adopt_domain_driven_design",
                category=
                    "architecture",
                priority=
                    "high"
            ),
            EnterpriseRecommendation(
                recommendation=
                    "implement_event_streaming",
                category=
                    "integration",
                priority=
                    "high"
            ),
            EnterpriseRecommendation(
                recommendation=
                    "establish_api_governance",
                category=
                    "governance",
                priority=
                    "medium"
            )
        ]
