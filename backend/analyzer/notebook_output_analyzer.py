from dataclasses import dataclass


@dataclass
class NotebookOutput:

    output_type: str

    confidence: float


class NotebookOutputAnalyzer:

    OUTPUT_PATTERNS = {

        "prediction": [
            ".predict(",
            "predict("
        ],

        "visualization": [
            "plt.",
            "sns.",
            ".plot("
        ],

        "table": [
            "DataFrame(",
            ".head(",
            ".describe("
        ],

        "file": [
            ".to_csv(",
            ".to_excel(",
            ".save("
        ]
    }

    def analyze(
        self,
        notebook
    ):

        notebook_text = ""

        for cell in notebook.get(
            "cells",
            []
        ):

            notebook_text += "".join(
                cell.get(
                    "source",
                    []
                )
            )

        outputs = []

        for (
            output_type,
            patterns
        ) in self.OUTPUT_PATTERNS.items():

            for pattern in patterns:

                if pattern in notebook_text:

                    outputs.append(

                        NotebookOutput(

                            output_type=
                                output_type,

                            confidence=
                                0.90
                        )
                    )

                    break

        return outputs
