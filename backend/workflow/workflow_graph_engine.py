from dataclasses import dataclass


@dataclass
class WorkflowNode:

    node_id: str

    operation: str

    dependencies: list[str]


@dataclass
class WorkflowGraph:

    nodes: list[WorkflowNode]


class WorkflowGraphEngine:

    def build(
        self,
        ir
    ):

        return WorkflowGraph(

            nodes=[

                WorkflowNode(

                    node_id="load_data",

                    operation="load_dataframe",

                    dependencies=[]
                ),

                WorkflowNode(

                    node_id="predict",

                    operation="model_prediction",

                    dependencies=[
                        "load_data"
                    ]
                )
            ]
        )
