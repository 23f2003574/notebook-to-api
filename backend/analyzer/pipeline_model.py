from dataclasses import dataclass
from typing import List


@dataclass
class PipelineStage:
    name: str
    dependencies: List[str]


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