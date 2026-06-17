from dataclasses import dataclass
from typing import List

from .pipeline_endpoint_spec import (
    PipelineEndpointSpec
)

from .notebook_metadata_analyzer import (
    NotebookMetadataAnalyzer
)

from .cell_classifier import (
    CellClassifier
)


@dataclass
class PipelineStage:
    name: str

    dependencies: List[str]

    defined_variables: List[str]

    used_variables: List[str]

    dependency_variables: List[str]


@dataclass
class PipelineArtifact:
    name: str
    artifact_type: str


@dataclass
class ExecutionPipeline:
    stages: List[PipelineStage]

    notebook_metadata_analyzer: NotebookMetadataAnalyzer = (
        None
    )

    cell_classifier: CellClassifier = (
        None
    )

    def __post_init__(self):

        if (
            self.notebook_metadata_analyzer
            is None
        ):
            self.notebook_metadata_analyzer = (
                NotebookMetadataAnalyzer()
            )

        if (
            self.cell_classifier
            is None
        ):
            self.cell_classifier = (
                CellClassifier()
            )

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

    def execution_boundaries(
        self,
        graph
    ):

        return {
            "entry_points":
                graph.entry_points(),

            "terminal_nodes":
                graph.terminal_nodes()
        }

    def input_artifacts(self):

        return [
            PipelineArtifact(
                name=name,
                artifact_type="input"
            )
            for name in self.pipeline_inputs()
        ]

    def output_artifacts(self):

        return [
            PipelineArtifact(
                name=name,
                artifact_type="output"
            )
            for name in self.pipeline_outputs()
        ]

    def intermediate_artifacts(self):

        return [
            PipelineArtifact(
                name=name,
                artifact_type="intermediate"
            )
            for name in self.intermediate_variables()
        ]

    def artifact_inventory(self):

        return {
            "inputs": [
                artifact.name
                for artifact
                in self.input_artifacts()
            ],

            "outputs": [
                artifact.name
                for artifact
                in self.output_artifacts()
            ],

            "intermediates": [
                artifact.name
                for artifact
                in self.intermediate_artifacts()
            ]
        }

    def io_contract(self):

        return {
            "required_inputs":
                self.pipeline_inputs(),

            "produced_outputs":
                self.pipeline_outputs()
        }

    def endpoint_spec(
        self,
        execution_plan
    ):

        return PipelineEndpointSpec(
            endpoint_name="run_pipeline",

            input_fields=
                self.pipeline_inputs(),

            output_fields=
                self.pipeline_outputs(),

            execution_stages=
                execution_plan.stage_count(),

            parallelism_score=
                execution_plan.parallelism_score()
        )

    def endpoint_summary(
        self,
        execution_plan
    ):

        spec = self.endpoint_spec(
            execution_plan
        )

        return {
            "endpoint":
                spec.endpoint_name,

            "inputs":
                spec.input_fields,

            "outputs":
                spec.output_fields,

            "execution_stages":
                spec.execution_stages,

            "parallelism_score":
                spec.parallelism_score
        }

    def notebook_metadata(
        self,
        notebook_name,
        notebook
    ):

        return (
            self
            .notebook_metadata_analyzer
            .analyze(
                notebook_name,
                notebook
            )
        )

    def classify_cell(
        self,
        cell,
        cell_index
    ):

        return (
            self
            .cell_classifier
            .classify(
                cell,
                cell_index
            )
        )