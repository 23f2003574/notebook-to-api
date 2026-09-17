from dataclasses import dataclass


@dataclass
class RecoveryPolicy:

    task_id: str

    retry_count: int

    backoff_strategy: str

    on_failure: str


@dataclass
class WorkflowRecoveryPlan:

    policies: list[RecoveryPolicy]


class WorkflowFailureRecoveryPlanner:

    def build(
        self,
        execution_plan
    ):

        return WorkflowRecoveryPlan(

            policies=[

                RecoveryPolicy(

                    task_id=
                        "predict",

                    retry_count=
                        3,

                    backoff_strategy=
                        "exponential",

                    on_failure=
                        "checkpoint_restore"
                )
            ]
        )
