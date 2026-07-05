from dataclasses import dataclass


@dataclass
class ExecutionStage:

    stage_id: int

    tasks: list[str]

    parallel: bool


@dataclass
class WorkflowExecutionPlan:

    stages: list[ExecutionStage]


class WorkflowExecutionPlanner:

    def build(
        self,
        workflow,
        optimized_workflow
    ):

        return WorkflowExecutionPlan(

            stages=[

                ExecutionStage(

                    stage_id=1,

                    tasks=[

                        "load_data"
                    ],

                    parallel=False
                ),

                ExecutionStage(

                    stage_id=2,

                    tasks=[

                        "predict",

                        "generate_report"
                    ],

                    parallel=True
                )
            ]
        )
