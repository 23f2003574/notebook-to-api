from dataclasses import dataclass
from typing import Set

from .variable_tracker import (
    VariableTracker
)


@dataclass
class CellAnalysis:
    cell_id: int
    defined_variables: Set[str]
    used_variables: Set[str]


class CellAnalyzer:

    def __init__(self):
        self.variable_tracker = (
            VariableTracker()
        )

    def analyze_cell(
        self,
        cell_id: int,
        source_code: str
    ) -> CellAnalysis:

        usage = (
            self.variable_tracker
            .analyze(source_code)
        )

        return CellAnalysis(
            cell_id=cell_id,
            defined_variables=usage.defined,
            used_variables=usage.used
        )