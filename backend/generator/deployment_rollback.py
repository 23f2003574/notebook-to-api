from dataclasses import dataclass


@dataclass
class RollbackStep:

    order: int

    action: str


@dataclass
class DeploymentRollback:

    target: str

    steps: list[RollbackStep]


class DeploymentRollbackGenerator:

    def generate(
        self,
        execution_plan
    ):

        rollback_steps = [

            RollbackStep(
                order=1,

                action=
                    "Stop deployment"
            ),

            RollbackStep(
                order=2,

                action=
                    "Restore previous release"
            ),

            RollbackStep(
                order=3,

                action=
                    "Verify service health"
            )
        ]

        return DeploymentRollback(
            target=
                execution_plan.target,

            steps=
                rollback_steps
        )