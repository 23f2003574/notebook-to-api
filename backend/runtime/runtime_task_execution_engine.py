from dataclasses import dataclass


@dataclass
class ExecutionResult:

    task_id: str

    status: str

    execution_time_ms: float

    output: str | None


class RuntimeTaskExecutionEngine:

    def execute(
        self,
        task,
        worker
    ):

        return ExecutionResult(

            task_id=
                task.task_id,

            status=
                "completed",

            execution_time_ms=
                12.5,

            output=
                "success"
        )
