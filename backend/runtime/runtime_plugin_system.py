from dataclasses import dataclass
from typing import Protocol


class RuntimePlugin(Protocol):

    name: str

    def initialize(
        self
    ) -> None:
        ...


@dataclass
class PluginRegistry:

    plugins: list[RuntimePlugin]


class RuntimePluginSystem:

    def create_registry(
        self
    ):

        return PluginRegistry(

            plugins=[]
        )
