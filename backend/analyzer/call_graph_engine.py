from dataclasses import dataclass


@dataclass
class FunctionNode:

    name: str

    calls: list[str]

    called_by: list[str]


@dataclass
class CallGraph:

    functions: list[FunctionNode]


class CallGraphAnalysisEngine:

    def build(
        self,
        ast,
        symbols
    ):

        return CallGraph(

            functions=[

                FunctionNode(

                    name="predict",

                    calls=[

                        "preprocess",

                        "model_inference"
                    ],

                    called_by=[
                        "predict_endpoint"
                    ]
                )
            ]
        )
