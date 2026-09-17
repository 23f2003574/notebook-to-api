from dataclasses import dataclass


@dataclass
class NotebookIntent:

    intent: str

    confidence: float


class NotebookIntentAnalyzer:

    INTENT_PATTERNS = {

        "classification": [
            "Classifier",
            "classification_report",
            ".predict_proba("
        ],

        "regression": [
            "LinearRegression",
            "RandomForestRegressor",
            "mean_squared_error"
        ],

        "clustering": [
            "KMeans",
            "DBSCAN",
            "AgglomerativeClustering"
        ],

        "forecasting": [
            "ARIMA",
            "Prophet",
            "forecast"
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

        for (
            intent,
            patterns
        ) in self.INTENT_PATTERNS.items():

            for pattern in patterns:

                if pattern in notebook_text:

                    return NotebookIntent(

                        intent=
                            intent,

                        confidence=
                            0.90
                    )

        return NotebookIntent(

            intent=
                "unknown",

            confidence=
                0.50
        )
