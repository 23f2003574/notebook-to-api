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