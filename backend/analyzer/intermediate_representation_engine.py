from dataclasses import dataclass


@dataclass
class IRInstruction:

    opcode: str

    operands: list[str]

    result: str | None


@dataclass
class IntermediateRepresentation:

    instructions: list[IRInstruction]


class IntermediateRepresentationEngine:

    def generate(
        self,
        ssa_program
    ):

        return IntermediateRepresentation(

            instructions=[

                IRInstruction(

                    opcode=
                        "LOAD",

                    operands=[
                        "input_dataframe"
                    ],

                    result=
                        "%0"
                ),

                IRInstruction(

                    opcode=
                        "CALL",

                    operands=[
                        "predict",
                        "%0"
                    ],

                    result=
                        "%1"
                ),

                IRInstruction(

                    opcode=
                        "RETURN",

                    operands=[
                        "%1"
                    ],

                    result=
                        None
                )
            ]
        )
