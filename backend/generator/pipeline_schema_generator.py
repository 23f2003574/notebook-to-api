from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)
from .pipeline_metadata import (
    PipelineMetadata,
    PipelineFieldMetadata
)
from .openapi_schema_generator import (
    OpenAPISchemaGenerator
)
from .pipeline_contract_validator import (
    PipelineContractValidator
)
from .sdk_type_generator import (
    SDKTypeGenerator
)
from .typescript_interface_generator import (
    TypeScriptInterfaceGenerator
)
from .typescript_client_generator import (
    TypeScriptClientGenerator
)


class PipelineSchemaGenerator:

    def __init__(
        self
    ):

        self.openapi_generator = (
            OpenAPISchemaGenerator()
        )

        self.contract_validator = (
            PipelineContractValidator()
        )

        self.sdk_type_generator = (
            SDKTypeGenerator()
        )

        self.ts_generator = (
            TypeScriptInterfaceGenerator()
        )

        self.ts_client_generator = (
            TypeScriptClientGenerator()
        )

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

    def generate_openapi_schema(
        self,
        spec
    ):

        metadata = (
            self.generate_metadata(
                spec
            )
        )

        schema = (
            self.openapi_generator
            .generate_schema(
                metadata
            )
        )

        self.contract_validator\
            .validate_schema(
                spec,
                schema
            )

        return schema

    def generate_sdk_types(
        self,
        spec
    ):

        metadata = (
            self.generate_metadata(
                spec
            )
        )

        return (
            self.sdk_type_generator
            .generate_types(
                metadata
            )
        )

    def generate_typescript_interfaces(
        self,
        spec
    ):

        sdk_types = (
            self.generate_sdk_types(
                spec
            )
        )

        request_interface = (
            self.ts_generator
            .generate_interface(
                spec.request_model_name(),
                sdk_types[
                    "request_types"
                ]
            )
        )

        response_interface = (
            self.ts_generator
            .generate_interface(
                spec.response_model_name(),
                sdk_types[
                    "response_types"
                ]
            )
        )

        return {
            "request":
                request_interface,

            "response":
                response_interface
        }

    def generate_typescript_client(
        self,
        spec
    ):

        return (
            self.ts_client_generator
            .generate_method(
                spec
            )
        )
