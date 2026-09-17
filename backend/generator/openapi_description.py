from dataclasses import dataclass


@dataclass
class OpenAPIDescription:

    summary: str

    description: str

    tags: list[str]


class OpenAPIDescriptionGenerator:

    def generate(
        self,
        endpoint
    ):

        return OpenAPIDescription(

            summary=
                (
                    f"{endpoint.name} endpoint"
                ),

            description=
                (
                    f"Automatically generated "
                    f"endpoint for "
                    f"{endpoint.name}"
                ),

            tags=[
                "generated-api"
            ]
        )
