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

            stages.append(
                PipelineStage(
                    name=node_name,
                    dependencies=sorted(
                        node.dependencies
                    )
                )
            )

        return ExecutionPipeline(
            stages=stages
        )