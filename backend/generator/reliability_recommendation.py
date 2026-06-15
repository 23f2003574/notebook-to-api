from dataclasses import dataclass


@dataclass
class ReliabilityRecommendation:

    priority: str

    recommendation: str

    rationale: str


class ReliabilityRecommendationEngine:

    def generate(
        self,
        analysis
    ):

        recommendations = []

        for action in (
            analysis.prevention_actions
        ):

            recommendations.append(

                ReliabilityRecommendation(

                    priority="high",

                    recommendation=
                        action,

                    rationale=
                        (
                            "Derived from "
                            "post-incident analysis"
                        )
                )
            )

        if not recommendations:

            recommendations.append(

                ReliabilityRecommendation(

                    priority="low",

                    recommendation=
                        "Maintain current deployment process",

                    rationale=
                        "No significant issues detected"
                )
            )

        return recommendations