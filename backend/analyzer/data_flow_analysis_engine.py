from dataclasses import dataclass


@dataclass
class VariableFlow:

    variable: str

    defined_at: int

    used_at: list[int]

    escapes_scope: bool


@dataclass
class DataFlowGraph:

    variables: list[VariableFlow]


class DataFlowAnalysisEngine:

    def analyze(
        self,
        ast,
        cfg,
        symbols
    ):

        return DataFlowGraph(

            variables=[

                VariableFlow(

                    variable="prediction",

                    defined_at=5,

                    used_at=[8, 10],

                    escapes_scope=True
                )
            ]
        )
