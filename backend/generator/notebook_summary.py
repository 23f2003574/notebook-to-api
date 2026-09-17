from dataclasses import dataclass


@dataclass
class NotebookSummary:

    title: str

    summary: str


class NotebookSummaryGenerator:

    def generate(
        self,
        understanding
    ):

        intent = (
            understanding
            .intent
            .intent
        )

        model_names = [

            model.model_name

            for model

            in understanding.models
        ]

        summary = (
            f"This notebook performs "
            f"{intent} and uses "
            f"{', '.join(model_names) or 'unknown models'}."
        )

        return NotebookSummary(

            title=
                "Notebook Summary",

            summary=
                summary
        )
