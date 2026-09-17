from dataclasses import dataclass


@dataclass
class InfrastructureRecommendation:

    cpu: str

    memory: str

    storage: str

    recommendation_level: str


class InfrastructureRecommendationEngine:

    def generate(
        self
    ):

        return InfrastructureRecommendation(

            cpu=
                "1 vCPU",

            memory=
                "512 MB",

            storage=
                "1 GB",

            recommendation_level=
                "starter"
        )
