from dataclasses import dataclass


@dataclass
class ContainerRecommendation:

    container_required: bool

    container_runtime: str

    image_strategy: str

    confidence: float


class ContainerRecommendationEngine:

    def generate(
        self
    ):

        return ContainerRecommendation(

            container_required=
                True,

            container_runtime=
                "docker",

            image_strategy=
                "single-stage",

            confidence=
                0.95
        )
