from dataclasses import dataclass


@dataclass
class RuntimeState:

    execution_id: str

    active_tasks: int

    completed_tasks: int

    failed_tasks: int

    runtime_status: str


class RuntimeStateManagementEngine:

    def initialize(
        self,
        execution_id: str
    ):

        return RuntimeState(

            execution_id=
                execution_id,

            active_tasks=
                0,

            completed_tasks=
                0,

            failed_tasks=
                0,

            runtime_status=
                "running"
        )
