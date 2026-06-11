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
