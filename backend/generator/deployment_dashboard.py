from dataclasses import dataclass


@dataclass
class DeploymentDashboard:

    health_score: int

    readiness_score: int

    reliability_score: int

    risk_level: str

    active_alerts: int

    active_incidents: int


class DeploymentDashboardGenerator:

    def generate(
        self,
        health,
        readiness,
        risk,
        alert,
        incident,
        metrics
    ):

        return DeploymentDashboard(

            health_score=
                health.score,

            readiness_score=
                readiness.score,

            reliability_score=
                metrics.reliability_score,

            risk_level=
                risk.level,

            active_alerts=
                int(
                    alert.notify
                ),

            active_incidents=
                int(
                    incident.severity
                    != "normal"
                )
        )