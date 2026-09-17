from dataclasses import dataclass


@dataclass
class OpenAPIDocumentation:

    endpoint_name: str

    summary: str

    description: str

    tags: list[str]


class OpenAPIDocumentationEngine:

    def generate(
        self,
        endpoint_name
    ):

        return OpenAPIDocumentation(

            endpoint_name=
                endpoint_name,

            summary=
                f"{endpoint_name} endpoint",

            description=
                (
                    f"Generated API endpoint "
                    f"for {endpoint_name}"
                ),

            tags=[
                "generated-api"
            ]
        )
