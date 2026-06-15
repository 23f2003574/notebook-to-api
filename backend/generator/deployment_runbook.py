from dataclasses import dataclass


@dataclass
class RunbookStep:

    title: str

    action: str


@dataclass
class DeploymentRunbook:

    target: str

    steps: list[RunbookStep]


class DeploymentRunbookGenerator:

    def generate(
        self,
        execution_plan
    ):

        runbook_steps = []

        for step in (
            execution_plan.steps
        ):

            runbook_steps.append(

                RunbookStep(
                    title=step.name,

                    action=
                        step.description
                )
            )

        return DeploymentRunbook(
            target=
                execution_plan.target,

            steps=
                runbook_steps
        )