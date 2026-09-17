from dataclasses import dataclass


@dataclass
class NotebookModel:

    model_name: str

    framework: str

    confidence: float


class NotebookModelAnalyzer:

    MODEL_PATTERNS = {

        "RandomForestClassifier":
            "scikit-learn",

        "RandomForestRegressor":
            "scikit-learn",

        "LinearRegression":
            "scikit-learn",

        "LogisticRegression":
            "scikit-learn",

        "XGBClassifier":
            "xgboost",

        "XGBRegressor":
            "xgboost",

        "LGBMClassifier":
            "lightgbm",

        "LGBMRegressor":
            "lightgbm",

        "Sequential":
            "tensorflow",

        "torch.nn":
            "pytorch"
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

        models = []

        for (
            model,
            framework
        ) in self.MODEL_PATTERNS.items():

            if model in notebook_text:

                models.append(

                    NotebookModel(

                        model_name=
                            model,

                        framework=
                            framework,

                        confidence=
                            0.95
                    )
                )

        return models
