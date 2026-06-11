from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)
from .pipeline_schema_generator import (
    PipelineSchemaGenerator
)


class PipelineModelGenerator:

    def __init__(
        self
    ):

        self.schema_generator = (
            PipelineSchemaGenerator()
        )

    def generate_request_model(
        self,
        spec: PipelineEndpointSpec
    ):

        field_block = (
            self.schema_generator
            .generate_fields(
                spec.input_fields
            )
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

    def generate_response_model(
        self,
        spec: PipelineEndpointSpec
    ):

        field_block = (
            self.schema_generator
            .generate_fields(
                spec.output_fields
            )
        )

        return dedent(
            f"""
            from pydantic import BaseModel


            class {
                spec.response_model_name()
            }(
                BaseModel
            ):
                {field_block}
            """
        )
