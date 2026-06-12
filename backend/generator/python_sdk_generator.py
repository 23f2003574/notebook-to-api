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
                    base_url: str,
                    timeout: int = 30,
                    max_retries: int = 3,
                    api_key: str | None = None,
                    bearer_token: str | None = None
                ):

                    self.base_url = (
                        base_url
                    )

                    self.timeout = (
                        timeout
                    )

                    self.max_retries = (
                        max_retries
                    )

                    self.api_key = (
                        api_key
                    )

                    self.bearer_token = (
                        bearer_token
                    )

                def build_headers(
                    self
                ):

                    headers = {{}}

                    if self.api_key:

                        headers[
                            "X-API-Key"
                        ] = (
                            self.api_key
                        )

                    if self.bearer_token:

                        headers[
                            "Authorization"
                        ] = (
                            "Bearer "
                            +
                            self.bearer_token
                        )

                    return headers

                def {
                    spec.client_method_name()
                }(
                    self,
                    payload: dict
                ):

                    url = (
                        self.base_url
                        +
                        "/{spec.endpoint_name}"
                    )

                    last_exception = None

                    for _ in range(
                        self.max_retries
                    ):

                        try:

                            response = (
                                requests.post(
                                    url,

                                    json=payload,

                                    headers=
                                        self.build_headers(),

                                    timeout=
                                        self.timeout
                                )
                            )

                            break

                        except Exception as e:

                            last_exception = e

                    else:

                        raise last_exception

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

    def generate_retry_loop(
        self
    ):

        return """
last_exception = None

for _ in range(
    self.max_retries
):

    try:

        response = (
            requests.post(
                url,
                json=payload,
                timeout=self.timeout
            )
        )

        return response

    except Exception as e:

        last_exception = e

raise last_exception
"""


