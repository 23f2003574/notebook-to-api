from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)
from .pipeline_metadata import (
    PipelineMetadata,
    PipelineFieldMetadata
)


class PipelineSchemaGenerator:

    def infer_field_type(
        self,
        field_name: str
    ):

        numeric_keywords = {
            "count",
            "size",
            "total",
            "num"
        }

        for keyword in (
            numeric_keywords
        ):

            if keyword in (
                field_name.lower()
            ):
                return "int"

        return "str"

    def generate_fields(
        self,
        field_names
    ):

        fields = []

        for field_name in (
            field_names
        ):

            field_type = (
                self.infer_field_type(
                    field_name
                )
            )

            fields.append(
                f"{field_name}: {field_type}"
            )

        return "\n".join(
            fields
        )

    def generate_metadata(
        self,
        spec
    ):

        inputs = []

        outputs = []

        for field_name in (
            spec.input_fields
        ):

            inputs.append(
                PipelineFieldMetadata(
                    name=field_name,

                    field_type=
                        self.infer_field_type(
                            field_name
                        )
                )
            )

        for field_name in (
            spec.output_fields
        ):

            outputs.append(
                PipelineFieldMetadata(
                    name=field_name,

                    field_type=
                        self.infer_field_type(
                            field_name
                        )
                )
            )

        return PipelineMetadata(
            endpoint_name=
                spec.endpoint_name,

            inputs=
                inputs,

            outputs=
                outputs
        )
