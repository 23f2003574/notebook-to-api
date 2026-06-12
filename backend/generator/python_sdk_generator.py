from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PythonSDKGenerator:

    def generate_client(
        self,
        spec: PipelineEndpointSpec
    ):

        return dedent(
            f"""
            import requests

            from .exceptions import (
                APIError
            )


            class {
                spec.python_client_name()
            }:

                def __init__(
                    self,
                    base_url: str
                ):

                    self.base_url = (
                        base_url
                    )

                def {
                    spec.client_method_name()
                }(
                    self,
                    payload: dict
                ):

                    response = (
                        requests.post(
                            self.base_url
                            +
                            "/{spec.endpoint_name}",

                            json=payload
                        )
                    )

                    if not response.ok:

                        raise APIError(
                            status_code=
                                response.status_code,

                            message=
                                response.text
                        )

                    return response.json()
            """
        )

    def generate_error_handler(
        self
    ):

        return """
if not response.ok:

    raise APIError(
        status_code=
            response.status_code,

        message=
            response.text
    )
"""

