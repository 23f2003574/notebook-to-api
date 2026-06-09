from dataclasses import dataclass
from typing import List


@dataclass
class PipelineStage:
    name: str

    dependencies: List[str]

    defined_variables: List[str]

    used_variables: List[str]

    dependency_variables: List[str]


@dataclass
class ExecutionPipeline:
    stages: List[PipelineStage]

    def stage_names(self):
        return [
            stage.name
            for stage
            in self.stages
        ]

    def stage_count(self):
        return len(
            self.stages
        )

    def pipeline_inputs(self):

        produced = set()

        consumed = set()

        for stage in self.stages:

            produced.update(
                stage.defined_variables
            )

            consumed.update(
                stage.used_variables
            )

        return sorted(
            consumed - produced
        )

    def pipeline_outputs(self):

        produced = set()

        consumed = set()

        for stage in self.stages:

            produced.update(
                stage.defined_variables
            )

            consumed.update(
                stage.used_variables
            )

        return sorted(
            produced - consumed
        )

    def intermediate_variables(self):

        produced = set()

        consumed = set()

        for stage in self.stages:

            produced.update(
                stage.defined_variables
            )

            consumed.update(
                stage.used_variables
            )

        return sorted(
            produced & consumed
        )

    def pipeline_summary(self):

        return {
            "stage_count":
                self.stage_count(),

            "inputs":
                self.pipeline_inputs(),

            "outputs":
                self.pipeline_outputs(),

            "intermediate_variables":
                self.intermediate_variables()
        }