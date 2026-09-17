from dataclasses import dataclass


@dataclass
class InferredType:

    symbol: str

    inferred_type: str

    confidence: float


class TypeInferenceEngine:

    def infer(
        self,
        ast,
        symbols
    ):

        return [

            InferredType(

                symbol="predict",

                inferred_type="Callable",

                confidence=0.98
            )
        ]
