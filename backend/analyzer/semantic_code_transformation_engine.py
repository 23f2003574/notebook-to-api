from dataclasses import dataclass


@dataclass
class CodeTransformation:

    transformation: str

    target: str

    description: str


@dataclass
class TransformationPlan:

    transformations: list[CodeTransformation]


class SemanticCodeTransformationEngine:

    def generate(
        self,
        ast,
        optimization_pipeline
    ):

        return TransformationPlan(

            transformations=[

                CodeTransformation(

                    transformation=
                        "remove_dead_code",

                    target=
                        "unused_assignments",

                    description=
                        "Eliminate unreachable statements."
                ),

                CodeTransformation(

                    transformation=
                        "inline_function",

                    target=
                        "small_helper",

                    description=
                        "Inline frequently-used helper."
                ),

                CodeTransformation(

                    transformation=
                        "optimize_imports",

                    target=
                        "module",

                    description=
                        "Remove unused imports."
                )
            ]
        )
