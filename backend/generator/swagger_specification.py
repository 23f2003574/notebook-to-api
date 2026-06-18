from dataclasses import dataclass


@dataclass
class SwaggerSpecification:

    title: str

    version: str

    paths: dict


class SwaggerSpecificationEngine:

    def generate(
        self,
        openapi_specification
    ):

        return SwaggerSpecification(

            title=
                openapi_specification.title,

            version=
                openapi_specification.version,

            paths=
                openapi_specification.paths
        )
