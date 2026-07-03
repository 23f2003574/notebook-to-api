from dataclasses import dataclass


@dataclass
class SSAVariable:

    original_name: str

    version: int

    ssa_name: str


@dataclass
class SSAProgram:

    variables: list[SSAVariable]


class StaticSingleAssignmentEngine:

    def build(
        self,
        cfg,
        symbols
    ):

        return SSAProgram(

            variables=[

                SSAVariable(

                    original_name=
                        "prediction",

                    version=1,

                    ssa_name=
                        "prediction_1"
                )
            ]
        )
