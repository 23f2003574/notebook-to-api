from .dependency_graph import (
    DependencyGraph
)

from .pipeline_model import (
    ExecutionPipeline,
    PipelineStage
)

from .cell_analyzer import (
    CellAnalyzer
)


class PipelineExtractor:

    def __init__(self):
        self.cell_analyzer = (
            CellAnalyzer()
        )

    def extract(
        self,
        graph: DependencyGraph,
        notebook_cells=None
    ) -> ExecutionPipeline:

        execution_order = (
            graph.execution_order()
        )

        stages = []

        cell_analysis_map = {}

        if notebook_cells:

            for idx, source_code in enumerate(
                notebook_cells
            ):

                analysis = (
                    self.cell_analyzer
                    .analyze_cell(
                        cell_id=idx,
                        source_code=source_code
                    )
                )

                cell_analysis_map[
                    f"cell_{idx}"
                ] = analysis

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

            analysis = (
                cell_analysis_map.get(
                    node_name
                )
            )

            stages.append(
                PipelineStage(
                    name=node_name,

                    dependencies=sorted(
                        node.dependencies
                    ),

                    defined_variables=sorted(
                        analysis.defined_variables
                    )
                    if analysis else [],

                    used_variables=sorted(
                        analysis.used_variables
                    )
                    if analysis else [],

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