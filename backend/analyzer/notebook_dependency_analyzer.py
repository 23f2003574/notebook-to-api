from typing import List

from .cell_analyzer import (
    CellAnalyzer,
    CellAnalysis
)

from .dependency_graph import (
    DependencyGraph
)


class NotebookDependencyAnalyzer:

    def __init__(self):
        pass

    def analyze(
        self,
        notebook_cells: List[str]
    ) -> DependencyGraph:

        analyses: List[
            CellAnalysis
        ] = []

        for idx, cell_source in enumerate(
            notebook_cells
        ):
            analyses.append(
                CellAnalyzer().analyze_cell(
                    cell_id=idx,
                    source_code=cell_source
                )
            )

        graph = DependencyGraph()

        for current_cell in analyses:

            graph.add_node(
                f"cell_{current_cell.cell_id}"
            )

            for previous_cell in analyses:

                if (
                    previous_cell.cell_id
                    >= current_cell.cell_id
                ):
                    continue

                shared_variables = (
                    current_cell.used_variables
                    &
                    previous_cell.defined_variables
                )

                if shared_variables:

                    graph.add_dependency(
                        f"cell_{current_cell.cell_id}",
                        f"cell_{previous_cell.cell_id}"
                    )

        return graph