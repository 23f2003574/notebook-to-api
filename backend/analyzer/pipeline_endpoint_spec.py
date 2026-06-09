from dataclasses import dataclass
from typing import List


@dataclass
class PipelineEndpointSpec:

    endpoint_name: str

    input_fields: List[str]

    output_fields: List[str]

    execution_stages: int

    parallelism_score: float