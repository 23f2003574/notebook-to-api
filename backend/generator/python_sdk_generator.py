from textwrap import dedent

from dataclasses import dataclass

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


@dataclass
class PythonSDK:

    package_name: str

    methods: list[str]

    version: str


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
                    payload: dict,
                    page: int = 1,
                    limit: int = 100
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

                                    params={{
                                        "page":
                                            page,

                                        "limit":
                                            limit
                                    }},

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


    def generate(
        self,
        sdk_methods
    ):

        return PythonSDK(

            package_name=
                "generated_sdk",

            methods=[

                method.method_name

                for method

                in sdk_methods
            ],

            version=
                "1.0.0"
        )
