from dataclasses import dataclass


@dataclass
class DeploymentRecommendation:

    primary_target: str

    alternatives: list[str]

    unsupported: list[str]


class DeploymentRecommender:

    PRIORITY_ORDER = [

        "docker-compose",

        "docker",

        "helm",

        "kubernetes",

        "terraform"
    ]

    def recommend(
        self,
        compatibility_results
    ):

        supported = [

            result.target

            for result
            in compatibility_results

            if result.supported
        ]

        unsupported = [

            result.target

            for result
            in compatibility_results

            if not result.supported
        ]

        primary = None

        for target in (
            self.PRIORITY_ORDER
        ):

            if target in supported:

                primary = target

                break

        alternatives = [

            target

            for target
            in supported

            if target != primary
        ]

        return (
            DeploymentRecommendation(
                primary_target=
                    primary,

                alternatives=
                    alternatives,

                unsupported=
                    unsupported
            )
        )