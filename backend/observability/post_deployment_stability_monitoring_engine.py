from dataclasses import dataclass


@dataclass
class PostDeploymentStability:

    deployment_id: str

    error_rate: float

    latency_ms: float

    burn_rate: float

    active_incidents: int

    stable: bool


class PostDeploymentStabilityMonitoringEngine:

    def evaluate(
        self,
        deployment_id: str,
        error_rate: float,
        latency_ms: float,
        burn_rate: float,
        active_incidents: int
    ):

        stable = (
            error_rate <= 1.0
            and latency_ms <= 1000
            and burn_rate <= 1.0
            and active_incidents == 0
        )

        return PostDeploymentStability(

            deployment_id=
                deployment_id,

            error_rate=
                error_rate,

            latency_ms=
                latency_ms,

            burn_rate=
                burn_rate,

            active_incidents=
                active_incidents,

            stable=
                stable
        )
