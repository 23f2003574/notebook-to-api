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
                    timeout: int = 30
                ):

                    self.base_url = (
                        base_url
                    )

                    self.timeout = (
                        timeout
                    )

                async def {
                    spec.client_method_name()
                }(
                    self,
                    payload: dict
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

                                json=payload
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
