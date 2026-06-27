from dataclasses import dataclass


@dataclass
class AIWorkflow:

    workflow_name: str

    stages: list[str]

    execution_strategy: str

    parallel_execution: bool


class AIWorkflowEngine:

    def generate(
        self
    ):

        return AIWorkflow(

            workflow_name=
                "agentic_request_processing",

            stages=[

                "request_analysis",

                "retrieval",

                "reasoning",

                "tool_execution",

                "response_generation"
            ],

            execution_strategy=
                "planner_executor",

            parallel_execution=
                True
        )
