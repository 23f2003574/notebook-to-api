from dataclasses import dataclass


@dataclass
class RolloutPromotionDecision:

    deployment_id: str

    current_traffic_percentage: int

    next_traffic_percentage: int

    deployment_healthy: bool

    action: str

    rollout_complete: bool


class ProgressiveRolloutPromotionEngine:

    def evaluate(
        self,
        deployment_id: str,
        current_traffic_percentage: int,
        deployment_healthy: bool
    ):

        if not deployment_healthy:

            next_traffic_percentage = (
                current_traffic_percentage
            )

            action = (
                "stop"
            )

            rollout_complete = (
                False
            )

        elif current_traffic_percentage >= 100:

            next_traffic_percentage = (
                100
            )

            action = (
                "complete"
            )

            rollout_complete = (
                True
            )

        else:

            rollout_stages = [
                1,
                5,
                10,
                25,
                50,
                100
            ]

            next_traffic_percentage = (
                100
            )

            for stage in rollout_stages:

                if stage > current_traffic_percentage:

                    next_traffic_percentage = (
                        stage
                    )

                    break

            action = (
                "promote"
            )

            rollout_complete = (
                next_traffic_percentage == 100
            )

        return RolloutPromotionDecision(

            deployment_id=
                deployment_id,

            current_traffic_percentage=
                current_traffic_percentage,

            next_traffic_percentage=
                next_traffic_percentage,

            deployment_healthy=
                deployment_healthy,

            action=
                action,

            rollout_complete=
                rollout_complete
        )
