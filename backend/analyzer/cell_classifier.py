from dataclasses import dataclass


@dataclass
class CellClassification:

    cell_index: int

    classification: str

    confidence: float


class CellClassifier:

    IMPORT_KEYWORDS = [
        "import ",
        "from "
    ]

    TRAINING_KEYWORDS = [
        ".fit(",
        "train("
    ]

    INFERENCE_KEYWORDS = [
        ".predict(",
        "predict("
    ]

    VISUALIZATION_KEYWORDS = [
        "plt.",
        "sns.",
        ".plot("
    ]

    def classify(
        self,
        cell,
        cell_index
    ):

        source = "".join(
            cell.get(
                "source",
                []
            )
        )

        classification = "unknown"
        confidence = 0.5

        if any(
            keyword in source
            for keyword in self.IMPORT_KEYWORDS
        ):

            classification = "import"
            confidence = 0.95

        elif any(
            keyword in source
            for keyword in self.TRAINING_KEYWORDS
        ):

            classification = "training"
            confidence = 0.90

        elif any(
            keyword in source
            for keyword in self.INFERENCE_KEYWORDS
        ):

            classification = "inference"
            confidence = 0.90

        elif any(
            keyword in source
            for keyword in self.VISUALIZATION_KEYWORDS
        ):

            classification = "visualization"
            confidence = 0.85

        return CellClassification(

            cell_index=
                cell_index,

            classification=
                classification,

            confidence=
                confidence
        )
