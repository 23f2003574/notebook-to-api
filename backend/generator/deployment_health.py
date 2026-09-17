from dataclasses import dataclass


@dataclass
class DeploymentHealth:

    target: str

    healthy: bool

    score: int

    message: str


class DeploymentHealthAnalyzer:

    HEALTH_SCORES = {

        "docker-compose": 100,

        "docker": 95,

        "helm": 85,

        "kubernetes": 80,

        "terraform": 75
    }

    def analyze(
        self,
        recommendation
    ):

        target = (
            recommendation
            .primary_target
        )

        score = (
            self.HEALTH_SCORES
            .get(
                target,
                50
            )
        )

        return DeploymentHealth(
            target=target,

            healthy=
                score >= 80,

            score=score,

            message=
                "Deployment target healthy"
        )