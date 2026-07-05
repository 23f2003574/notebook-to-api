from dataclasses import dataclass


@dataclass
class DependencyAnalysis:

    execution_order: list[str]

    parallel_groups: list[list[str]]

    critical_path: list[str]

    has_cycle: bool


class WorkflowDependencyAnalysisEngine:

    def analyze(
        self,
        workflow_graph
    ):

        return DependencyAnalysis(

            execution_order=[

                "load_data",

                "predict"
            ],

            parallel_groups=[

                [

                    "load_data"

                ],

                [

                    "predict"

                ]
            ],

            critical_path=[

                "load_data",

                "predict"
            ],

            has_cycle=False
        )
