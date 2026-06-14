from dataclasses import dataclass


@dataclass
class DeploymentMetrics:

    success_rate: float

    availability: float

    reliability_score: int

    slo_compliant: bool


class DeploymentMetricsAnalyzer:

    def analyze(
        self,
        health,
        readiness
    ):

        success_rate = (
            health.score / 100
        )

        availability = (
            readiness.score / 100
        )

        reliability_score = int(
            (
                success_rate
                +
                availability
            )
            *
            50
        )

        return DeploymentMetrics(
            success_rate=
                success_rate,

            availability=
                availability,

            reliability_score=
                reliability_score,

            slo_compliant=
                reliability_score
                >= 80
        )