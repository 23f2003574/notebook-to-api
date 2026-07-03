from dataclasses import dataclass


@dataclass
class OptimizationPass:

    name: str

    enabled: bool


@dataclass
class OptimizationPipeline:

    optimization_level: str

    passes: list[OptimizationPass]


class CompilerOptimizationPipeline:

    def build(
        self,
        ssa_program
    ):

        return OptimizationPipeline(

            optimization_level=
                "O2",

            passes=[

                OptimizationPass(

                    name=
                        "constant_propagation",

                    enabled=True
                ),

                OptimizationPass(

                    name=
                        "dead_code_elimination",

                    enabled=True
                ),

                OptimizationPass(

                    name=
                        "common_subexpression_elimination",

                    enabled=True
                ),

                OptimizationPass(

                    name=
                        "copy_propagation",

                    enabled=True
                )
            ]
        )
