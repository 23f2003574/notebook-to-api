from dataclasses import dataclass


@dataclass
class EnvironmentVariable:

    name: str

    required: bool

    default_value: str | None


class EnvironmentVariableEngine:

    def generate(
        self
    ):

        return [

            EnvironmentVariable(

                name=
                    "OPENAI_API_KEY",

                required=
                    True,

                default_value=
                    None
            ),

            EnvironmentVariable(

                name=
                    "HOST",

                required=
                    False,

                default_value=
                    "0.0.0.0"
            ),

            EnvironmentVariable(

                name=
                    "PORT",

                required=
                    False,

                default_value=
                    "8000"
            )
        ]
