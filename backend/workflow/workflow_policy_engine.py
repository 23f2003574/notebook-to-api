from dataclasses import dataclass


@dataclass
class WorkflowPolicy:

    policy_name: str

    execution_allowed: bool

    allowed_environments: list[str]

    max_parallel_tasks: int


class WorkflowPolicyEngine:

    def evaluate(
        self,
        workflow_id: str
    ):

        return WorkflowPolicy(

            policy_name=
                "default_policy",

            execution_allowed=
                True,

            allowed_environments=[

                "development",

                "staging",

                "production"
            ],

            max_parallel_tasks=
                8
        )
