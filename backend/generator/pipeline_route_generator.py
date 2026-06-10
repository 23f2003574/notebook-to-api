from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PipelineRouteGenerator:

    def generate_route(
        self,
        spec: PipelineEndpointSpec
    ):

        parameters = []

        for field in (
            spec.input_fields
        ):

            parameters.append(
                f"{field}: str"
            )

        parameter_string = (
            ", ".join(
                parameters
            )
        )

        return dedent(
            f"""
            @router.post(
                "/{spec.endpoint_name}"
            )
            async def {
                spec.route_name()
            }(
                {parameter_string}
            ):

                return {{
                    "status":
                        "accepted"
                }}
            """
        )