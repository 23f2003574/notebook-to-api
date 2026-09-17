from dataclasses import dataclass


@dataclass
class TestStrategy:

    strategy: str

    unit_testing: bool

    integration_testing: bool

    end_to_end_testing: bool


class TestStrategyEngine:

    def generate(
        self
    ):

        return TestStrategy(

            strategy=
                "comprehensive",

            unit_testing=
                True,

            integration_testing=
                True,

            end_to_end_testing=
                True
        )
