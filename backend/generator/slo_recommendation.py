from dataclasses import dataclass


@dataclass
class SLORecommendation:

    availability_target: float

    latency_target_ms: int

    error_budget_percent: float

    reliability_tier: str


class SLORecommendationEngine:

    def generate(
        self
    ):

        return SLORecommendation(

            availability_target=
                99.9,

            latency_target_ms=
                500,

            error_budget_percent=
                0.1,

            reliability_tier=
                "production"
        )
