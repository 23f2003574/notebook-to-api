from typing import Dict

from .executable_stage import (
    ExecutableStage
)


class StageRegistry:

    def __init__(self):

        self.stages: Dict[
            str,
            ExecutableStage
        ] = {}

    def register(
        self,
        stage: ExecutableStage
    ):

        self.stages[
            stage.name
        ] = stage

    def get(
        self,
        stage_name: str
    ):

        return self.stages[
            stage_name
        ]

    def exists(
        self,
        stage_name: str
    ):

        return (
            stage_name
            in self.stages
        )