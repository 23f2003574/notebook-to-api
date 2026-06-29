from dataclasses import dataclass


@dataclass
class PlatformRecommendation:

    recommendation: str

    category: str

    priority: str


class PlatformRecommendationEngine:

    def generate(
        self
    ):

        return [
            PlatformRecommendation(
                recommendation=
                    "expand_golden_path_templates",
                category=
                    "developer_experience",
                priority=
                    "high"
            ),
            PlatformRecommendation(
                recommendation=
                    "enable_self_service_provisioning",
                category=
                    "platform_operations",
                priority=
                    "high"
            ),
            PlatformRecommendation(
                recommendation=
                    "introduce_platform_scorecards",
                category=
                    "governance",
                priority=
                    "medium"
            )
        ]
