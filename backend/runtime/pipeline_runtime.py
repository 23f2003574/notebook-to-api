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

    def all_values(
        self
    ):

        return dict(
            self.context
        )