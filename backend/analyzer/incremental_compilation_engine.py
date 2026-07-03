from dataclasses import dataclass


@dataclass
class CompilationUnit:

    unit_id: str

    fingerprint: str

    dirty: bool


@dataclass
class IncrementalCompilationPlan:

    units: list[CompilationUnit]


class IncrementalCompilationEngine:

    def plan(
        self,
        dependency_graph
    ):

        return IncrementalCompilationPlan(

            units=[

                CompilationUnit(

                    unit_id=
                        "cell_4",

                    fingerprint=
                        "a1f83e",

                    dirty=
                        True
                )
            ]
        )
