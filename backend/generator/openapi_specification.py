from dataclasses import dataclass


@dataclass
class OpenAPISpecification:

    title: str

    version: str

    paths: dict


class OpenAPISpecificationEngine:

    def generate(
        self,
        endpoint_name,
        request_schema,
        response_schema
    ):

        return OpenAPISpecification(

            title=
                endpoint_name,

            version=
                "1.0.0",

            paths={

                f"/{endpoint_name}": {

                    "post": {

                        "request_schema":
                            request_schema.title,

                        "response_schema":
                            response_schema.title
                    }
                }
            }
        )
