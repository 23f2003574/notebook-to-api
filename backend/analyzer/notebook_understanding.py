from dataclasses import dataclass


@dataclass
class NotebookUnderstanding:

    metadata: object

    intent: object

    models: list

    inputs: list

    outputs: list

    api_candidates: list


class NotebookUnderstandingEngine:

    def __init__(
        self,
        metadata_analyzer,
        intent_analyzer,
        model_analyzer,
        input_analyzer,
        output_analyzer,
        api_candidate_analyzer
    ):

        self.metadata_analyzer = (
            metadata_analyzer
        )

        self.intent_analyzer = (
            intent_analyzer
        )

        self.model_analyzer = (
            model_analyzer
        )

        self.input_analyzer = (
            input_analyzer
        )

        self.output_analyzer = (
            output_analyzer
        )

        self.api_candidate_analyzer = (
            api_candidate_analyzer
        )

    def analyze(
        self,
        notebook_name,
        notebook
    ):

        return NotebookUnderstanding(

            metadata=
                self.metadata_analyzer
                .analyze(
                    notebook_name,
                    notebook
                ),

            intent=
                self.intent_analyzer
                .analyze(
                    notebook
                ),

            models=
                self.model_analyzer
                .analyze(
                    notebook
                ),

            inputs=
                self.input_analyzer
                .analyze(
                    notebook
                ),

            outputs=
                self.output_analyzer
                .analyze(
                    notebook
                ),

            api_candidates=
                self.api_candidate_analyzer
                .analyze(
                    notebook
                )
        )
