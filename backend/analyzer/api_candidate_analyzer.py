from dataclasses import dataclass


@dataclass
class APICandidate:

    cell_index: int

    endpoint_name: str

    confidence: float


class APICandidateAnalyzer:

    API_PATTERNS = [

        ".predict(",

        "predict(",

        "def predict",

        "def infer",

        "def classify",

        "def forecast"
    ]

    def analyze(
        self,
        notebook
    ):

        candidates = []

        cells = notebook.get(
            "cells",
            []
        )

        for (
            index,
            cell
        ) in enumerate(cells):

            source = "".join(
                cell.get(
                    "source",
                    []
                )
            )

            for pattern in self.API_PATTERNS:

                if pattern in source:

                    candidates.append(

                        APICandidate(

                            cell_index=
                                index,

                            endpoint_name=
                                f"endpoint_{index}",

                            confidence=
                                0.95
                        )
                    )

                    break

        return candidates
