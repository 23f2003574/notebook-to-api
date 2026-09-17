from dataclasses import dataclass


@dataclass
class NotebookInput:

    name: str

    source: str

    confidence: float


class NotebookInputAnalyzer:

    INPUT_PATTERNS = [

        "pd.read_csv(",
        "pd.read_excel(",
        "input(",
        "argparse.",
        "request."
    ]

    def analyze(
        self,
        notebook
    ):

        inputs = []

        cells = notebook.get(
            "cells",
            []
        )

        for cell in cells:

            source = "".join(
                cell.get(
                    "source",
                    []
                )
            )

            for pattern in self.INPUT_PATTERNS:

                if pattern in source:

                    inputs.append(

                        NotebookInput(

                            name=
                                pattern,

                            source=
                                "detected",

                            confidence=
                                0.90
                        )
                    )

        return inputs
