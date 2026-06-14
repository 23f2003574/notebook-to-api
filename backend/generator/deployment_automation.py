from dataclasses import dataclass


@dataclass
class DeploymentAutomation:

    enabled: bool

    workflow_name: str

    stages: list[str]


class DeploymentAutomationEngine:

    def generate(
        self,
        execution_plan
    ):

        if (
            execution_plan.target
            == "blocked"
        ):

            return DeploymentAutomation(
                enabled=False,

                workflow_name=
                    "blocked",

                stages=[]
            )

        return DeploymentAutomation(
            enabled=True,

            workflow_name=
                (
                    execution_plan.target
                    + "-deployment"
                ),

            stages=[
                step.name

                for step
                in execution_plan.steps
            ]
        )