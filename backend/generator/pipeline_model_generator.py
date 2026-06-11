from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PipelineModelGenerator:

    def generate_request_model(
        self,
        spec: PipelineEndpointSpec
    ):

        fields = []

        for field in (
            spec.input_fields
        ):

            fields.append(
                f"{field}: str"
            )

        field_block = "\n".join(
            fields
        )

        return dedent(
            f"""
            from pydantic import BaseModel


            class {
                spec.request_model_name()
            }(
                BaseModel
            ):
                {field_block}
            """
        )
