from dataclasses import dataclass
from typing import Callable


@dataclass
class ExecutableStage:

    name: str

    function: Callable

    output_key: str | None = None

    max_retries: int = 0

    def execute(
        self,
        runtime
    ):

        result = self.function(
            runtime
        )

        if self.output_key:

            runtime.set_value(
                self.output_key,
                result
            )

        return result