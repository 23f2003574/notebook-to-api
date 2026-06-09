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

    def _excluded_input_symbols(self):

        return {
            "print",
            "len",
            "str",
            "int",
            "float",
            "bool",
            "dict",
            "list",
            "set",
            "tuple",

            "pd",
            "np",

            "load_data",
            "preprocess",
            "train",
            "predict"
        }

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

        inputs = (
            consumed - produced
        )

        inputs = (
            inputs
            -
            self._excluded_input_symbols()
        )

        return sorted(
            inputs
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

    def partition_summary(
        self,
        graph
    ):

        return {
            "partition_count":
                graph.partition_count(),

            "partitions":
                graph.connected_components(),

            "largest_partition_size":
                graph.largest_partition_size()
        }