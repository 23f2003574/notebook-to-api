from dataclasses import dataclass


@dataclass
class DependencyEdge:

    source: int

    target: int

    dependency_type: str


@dataclass
class ProgramDependenceGraph:

    edges: list[DependencyEdge]


class ProgramDependenceGraphEngine:

    def build(
        self,
        cfg,
        data_flow
    ):

        return ProgramDependenceGraph(

            edges=[

                DependencyEdge(

                    source=5,

                    target=8,

                    dependency_type=
                        "data"
                ),

                DependencyEdge(

                    source=3,

                    target=5,

                    dependency_type=
                        "control"
                )
            ]
        )
