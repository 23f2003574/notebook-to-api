from textwrap import dedent

from backend.runtime import (
    PipelineRuntime
)

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
                "/{spec.endpoint_name}",

                response_model=
                    {spec.response_model_name()}
            )
            async def {
                spec.route_name()
            }(
                {parameter_string}
            ):

                runtime = PipelineRuntime()

                result = executor.execute_pipeline(
                    stage_names=PIPELINE_STAGES,

                    runtime=runtime,

                    inputs={{
                        {",".join(
                            f'"{field}": {field}'
                            for field
                            in spec.input_fields
                        )}
                    }},

                    expected_outputs={
                        spec.output_fields
                    }
                )

                return result
            """
        )