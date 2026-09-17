from typing import Any, Dict


class PipelineRuntime:

    def __init__(self):

        self.context: Dict[
            str,
            Any
        ] = {}

    def set_value(
        self,
        key: str,
        value: Any
    ):

        self.context[key] = value

    def get_value(
        self,
        key: str
    ):

        return self.context.get(
            key
        )

    def has_value(
        self,
        key: str
    ):

        return key in self.context

    def clear(self):

        self.context.clear()

    def load_inputs(
        self,
        inputs: dict
    ):

        for key, value in (
            inputs.items()
        ):

            self.set_value(
                key,
                value
            )

    def all_values(
        self
    ):

        return dict(
            self.context
        )

    def export_outputs(
        self,
        output_keys
    ):

        return {
            key: self.get_value(
                key
            )
            for key
            in output_keys
            if self.has_value(
                key
            )
        }