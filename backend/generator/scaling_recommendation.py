from dataclasses import dataclass


@dataclass
class ScalingRecommendation:

    strategy: str

    min_instances: int

    max_instances: int

    auto_scaling: bool


class ScalingRecommendationEngine:

    def generate(
        self
    ):

        return ScalingRecommendation(

            strategy=
                "horizontal",

            min_instances=
                1,

            max_instances=
                5,

            auto_scaling=
                True
        )
