from dataclasses import dataclass


@dataclass
class RuntimeExecutionPlan:

    execution_id: str

    scheduled: bool

    resources_allocated: bool

    distributed: bool


class RuntimeOrchestrator:

    def orchestrate(
        self,
        execution_id: str
    ):

        return RuntimeExecutionPlan(

            execution_id=
                execution_id,

            scheduled=
                True,

            resources_allocated=
                True,

            distributed=
                False
        )
