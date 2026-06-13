from dataclasses import dataclass


@dataclass
class DeploymentCost:

    target: str

    complexity: str

    operational_cost: str

    score: int


class DeploymentCostAnalyzer:

    TARGET_COSTS = {

        "docker-compose": {
            "complexity": "low",
            "cost": "low",
            "score": 1
        },

        "docker": {
            "complexity": "low",
            "cost": "low",
            "score": 2
        },

        "kubernetes": {
            "complexity": "medium",
            "cost": "medium",
            "score": 5
        },

        "helm": {
            "complexity": "medium",
            "cost": "medium",
            "score": 6
        },

        "terraform": {
            "complexity": "high",
            "cost": "high",
            "score": 9
        }
    }

    def analyze(
        self,
        compatibility_results
    ):

        results = []

        for result in compatibility_results:

            if not result.supported:

                continue

            config = (
                self.TARGET_COSTS
                .get(
                    result.target
                )
            )

            if config is None:

                continue

            results.append(
                DeploymentCost(
                    target=
                        result.target,

                    complexity=
                        config[
                            "complexity"
                        ],

                    operational_cost=
                        config[
                            "cost"
                        ],

                    score=
                        config[
                            "score"
                        ]
                )
            )

        return sorted(
            results,
            key=lambda x: x.score
        )