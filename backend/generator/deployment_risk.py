from dataclasses import dataclass


@dataclass
class DeploymentRisk:

    level: str

    score: int

    factors: list[str]


class DeploymentRiskAnalyzer:

    def analyze(
        self,
        readiness,
        health
    ):

        score = 0

        factors = []

        if not readiness.ready:

            score += 50

            factors.append(
                "Deployment not ready"
            )

        if health.score < 80:

            score += 30

            factors.append(
                "Low deployment health"
            )

        if score >= 50:

            level = "high"

        elif score >= 20:

            level = "medium"

        else:

            level = "low"

        return DeploymentRisk(
            level=level,

            score=score,

            factors=factors
        )