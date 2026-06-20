from dataclasses import dataclass


@dataclass
class LoggingStrategy:

    log_level: str

    structured_logging: bool

    log_categories: list[str]


class LoggingStrategyEngine:

    def generate(
        self
    ):

        return LoggingStrategy(

            log_level=
                "INFO",

            structured_logging=
                True,

            log_categories=[

                "requests",

                "errors",

                "performance",

                "audit"
            ]
        )
