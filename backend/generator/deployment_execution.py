from dataclasses import dataclass


@dataclass
class DeploymentStep:

    order: int

    name: str

    description: str


@dataclass
class DeploymentExecutionPlan:

    target: str

    steps: list[DeploymentStep]


class DeploymentExecutionEngine:

    def generate(
        self,
        approval,
        deployment_plan
    ):

        if not approval.approved:

            return DeploymentExecutionPlan(
                target="blocked",
                steps=[]
            )

        steps = [

            DeploymentStep(
                order=1,

                name="validate",

                description=
                    "Validate deployment artifacts"
            ),

            DeploymentStep(
                order=2,

                name="package",

                description=
                    "Prepare deployment bundle"
            ),

            DeploymentStep(
                order=3,

                name="deploy",

                description=
                    (
                        f"Deploy via "
                        f"{deployment_plan.recommended_target}"
                    )
            ),

            DeploymentStep(
                order=4,

                name="verify",

                description=
                    "Verify deployment health"
            )
        ]

        return DeploymentExecutionPlan(
            target=
                deployment_plan
                .recommended_target,

            steps=
                steps
        )