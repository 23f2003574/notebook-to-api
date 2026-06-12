from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PythonAsyncSDKGenerator:

    def generate_client(
        self,
        spec: PipelineEndpointSpec
    ):

        return dedent(
            f"""
            import httpx

            from .exceptions import (
                APIError
            )


            class {
                spec.python_async_client_name()
            }:

                def __init__(
                    self,
                    base_url: str,
                    timeout: int = 30,
                    api_key: str | None = None,
                    bearer_token: str | None = None
                ):

                    self.base_url = (
                        base_url
                    )

                    self.timeout = (
                        timeout
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

                async def {
                    spec.client_method_name()
                }(
                    self,
                    payload: dict,
                    page: int = 1,
                    limit: int = 100
                ):

                    async with (
                        httpx.AsyncClient(
                            timeout=
                                self.timeout
                        )
                    ) as client:

                        response = (
                            await client.post(
                                self.base_url
                                +
                                "/{spec.endpoint_name}",

                                json=payload,

                                headers=
                                    self.build_headers(),

                                params={{
                                    "page":
                                        page,

                                    "limit":
                                        limit
                                }}
                            )
                        )


                    if not response.is_success:

                        raise APIError(
                            status_code=
                                response.status_code,

                            message=
                                response.text
                        )

                    return (
                        response.json()
                    )
            """

        )
