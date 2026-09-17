from dataclasses import dataclass


@dataclass
class RuntimeCheckpoint:

    checkpoint_id: str

    execution_id: str

    task_id: str

    state_snapshot: dict


class RuntimeCheckpointEngine:

    def create(
        self,
        execution_id: str,
        task_id: str,
        state_snapshot: dict
    ):

        return RuntimeCheckpoint(

            checkpoint_id=
                "checkpoint-001",

            execution_id=
                execution_id,

            task_id=
                task_id,

            state_snapshot=
                state_snapshot
        )

    def restore(
        self,
        checkpoint: RuntimeCheckpoint
    ):

        return checkpoint.state_snapshot
