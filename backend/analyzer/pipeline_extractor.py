from .dependency_graph import (
    DependencyGraph
)

from .pipeline_model import (
    ExecutionPipeline,
    PipelineStage
)


class PipelineExtractor:

    def extract(
        self,
        graph: DependencyGraph
    ) -> ExecutionPipeline:

        execution_order = (
            graph.execution_order()
        )

        stages = []

        for node_name in execution_order:

            node = graph.nodes[
                node_name
            ]

            dependency_variables = []

            for dependency in node.dependencies:

                dependency_variables.extend(
                    sorted(
                        node.dependency_reasons.get(
                            dependency,
                            set()
                        )
                    )
                )

            stages.append(
                PipelineStage(
                    name=node_name,

                    dependencies=sorted(
                        node.dependencies
                    ),

                    defined_variables=[],

                    used_variables=[],

                    dependency_variables=sorted(
                        set(
                            dependency_variables
                        )
                    )
                )
            )

        return ExecutionPipeline(
            stages=stages
        )