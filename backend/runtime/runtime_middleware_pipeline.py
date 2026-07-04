from dataclasses import dataclass
from typing import Protocol


class RuntimeMiddleware(Protocol):

    name: str

    def before_execution(
        self,
        context
    ):
        ...

    def after_execution(
        self,
        context,
        result
    ):
        ...


@dataclass
class MiddlewarePipeline:

    middlewares: list[RuntimeMiddleware]


class RuntimeMiddlewarePipeline:

    def create(
        self
    ):

        return MiddlewarePipeline(

            middlewares=[]
        )
