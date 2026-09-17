from dataclasses import dataclass


@dataclass
class DataPlatformReadiness:

    platform_readiness_score: float

    analytics_readiness_score: float

    governance_maturity_score: float

    platform_grade: str


class DataPlatformReadinessEngine:

    def generate(
        self
    ):

        return DataPlatformReadiness(

            platform_readiness_score=
                95.0,

            analytics_readiness_score=
                94.0,

            governance_maturity_score=
                93.0,

            platform_grade=
                "A"
        )
