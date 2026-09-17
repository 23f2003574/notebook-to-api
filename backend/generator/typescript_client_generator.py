from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class TypeScriptClientGenerator:

    def generate_method(
        self,
        spec: PipelineEndpointSpec
    ):

        request_type = (
            spec.request_model_name()
        )

        response_type = (
            spec.response_model_name()
        )

        return dedent(
            f"""
            export async function {
                spec.client_method_name()
            }(
                request: {
                    request_type
                }
            ): Promise<{
                response_type
            }> {{

                const response =
                    await fetch(
                        "/{spec.endpoint_name}",
                        {{
                            method: "POST",

                            headers: {{
                                "Content-Type":
                                    "application/json"
                            }},

                            body:
                                JSON.stringify(
                                    request
                                )
                        }}
                    );

                return await (
                    response.json()
                );
            }}
            """
        )
