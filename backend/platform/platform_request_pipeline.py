from dataclasses import dataclass
from typing import Protocol


class RequestPipelineStage(Protocol):

    name: str

    def process(
        self,
        request
    ):
        ...


@dataclass
class PlatformPipeline:

    stages: list[RequestPipelineStage]


class PlatformRequestPipeline:

    def create(
        self
    ):

        return PlatformPipeline(

            stages=[]
        )
