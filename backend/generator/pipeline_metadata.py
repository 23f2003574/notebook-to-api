from dataclasses import dataclass
from typing import List


@dataclass
class PipelineFieldMetadata:

    name: str

    field_type: str

    required: bool = True


@dataclass
class PipelineMetadata:

    endpoint_name: str

    inputs: List[
        PipelineFieldMetadata
    ]

    outputs: List[
        PipelineFieldMetadata
    ]

    def input_count(
        self
    ):

        return len(
            self.inputs
        )

    def output_count(
        self
    ):

        return len(
            self.outputs
        )

    def all_fields(
        self
    ):

        return (
            self.inputs
            +
            self.outputs
        )
