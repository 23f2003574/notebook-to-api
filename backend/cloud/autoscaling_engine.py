from dataclasses import dataclass


@dataclass
class ScalingPolicy:

    metric: str

    target_value: float

    min_replicas: int

    max_replicas: int


@dataclass
class ScalingDecision:

    desired_replicas: int

    reason: str


class AutoscalingEngine:

    def evaluate(
        self,
        policy: ScalingPolicy
    ):

        return ScalingDecision(

            desired_replicas=
                policy.min_replicas,

            reason=
                "Within target utilization."
        )
