from dataclasses import dataclass
from typing import Callable


@dataclass
class ExecutableStage:

    name: str

    function: Callable

    def execute(
        self,
        runtime
    ):

        return self.function(
            runtime
        )