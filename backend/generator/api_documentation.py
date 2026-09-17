from dataclasses import dataclass


@dataclass
class EndpointDocumentation:

    endpoint: str

    description: str

    parameters: list[str]

    returns: str


class APIDocumentationGenerator:

    def generate(
        self,
        endpoint
    ):

        return EndpointDocumentation(

            endpoint=
                endpoint.path,

            description=
                (
                    f"Generated API endpoint "
                    f"for {endpoint.name}"
                ),

            parameters=
                endpoint.parameters,

            returns=
                "JSON response"
        )
