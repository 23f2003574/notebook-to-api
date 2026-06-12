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
from .typescript_sdk_generator import (
    TypeScriptSDKGenerator
)
from .sdk_index_generator import (
    SDKIndexGenerator
)
from .typescript_package_generator import (
    TypeScriptPackageGenerator
)
from .sdk_project_generator import (
    SDKProjectGenerator
)
from .python_sdk_generator import (
    PythonSDKGenerator
)
from .python_model_generator import (
    PythonModelGenerator
)
from .python_package_generator import (
    PythonPackageGenerator
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

        self.ts_sdk_generator = (
            TypeScriptSDKGenerator()
        )

        self.sdk_index_generator = (
            SDKIndexGenerator()
        )

        self.package_generator = (
            TypeScriptPackageGenerator()
        )

        self.project_generator = (
            SDKProjectGenerator()
        )

        self.python_sdk_generator = (
            PythonSDKGenerator()
        )

        self.python_model_generator = (
            PythonModelGenerator()
        )

        self.python_package_generator = (
            PythonPackageGenerator()
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

    def generate_typescript_sdk(
        self,
        spec
    ):

        sdk_types = (
            self.generate_sdk_types(
                spec
            )
        )

        return (
            self.ts_sdk_generator
            .generate_sdk(
                spec,
                sdk_types
            )
        )

    def generate_sdk_index(
        self,
        specs
    ):

        module_names = []

        for spec in specs:

            module_names.append(
                spec.sdk_module_name()
            )

        return (
            self.sdk_index_generator
            .generate_index(
                module_names
            )
        )

    def generate_sdk_package(
        self,
        package_name: str
    ):

        return {
            "package_json":
                self.package_generator
                .generate_package_json(
                    package_name
                ),

            "tsconfig":
                self.package_generator
                .generate_tsconfig()
        }

    def generate_sdk_project(
        self,
        specs
    ):

        if not specs:

            raise ValueError(
                "At least one endpoint "
                "spec is required"
            )

        package_artifacts = (
            self.generate_sdk_package(
                specs[0]
                .npm_package_name()
            )
        )

        sdk_modules = {}

        for spec in specs:

            sdk_modules[
                spec.sdk_module_name()
            ] = (
                self.generate_typescript_sdk(
                    spec
                )
            )

        sdk_index = (
            self.generate_sdk_index(
                specs
            )
        )

        return (
            self.project_generator
            .generate_project(
                package_json=
                    package_artifacts[
                        "package_json"
                    ],

                tsconfig=
                    package_artifacts[
                        "tsconfig"
                    ],

                sdk_index=
                    sdk_index,

                sdk_modules=
                    sdk_modules
            )
        )

    def generate_python_sdk(
        self,
        spec
    ):

        return (
            self.python_sdk_generator
            .generate_client(
                spec
            )
        )

    def generate_python_models(
        self,
        spec
    ):

        sdk_types = (
            self.generate_sdk_types(
                spec
            )
        )

        request_model = (
            self.python_model_generator
            .generate_model(
                spec.request_model_name(),
                sdk_types[
                    "request_types"
                ]
            )
        )

        response_model = (
            self.python_model_generator
            .generate_model(
                spec.response_model_name(),
                sdk_types[
                    "response_types"
                ]
            )
        )

        return {
            "request":
                request_model,

            "response":
                response_model
        }

    def generate_python_package(
        self,
        spec
    ):

        client_code = (
            self.generate_python_sdk(
                spec
            )
        )

        models = (
            self.generate_python_models(
                spec
            )
        )

        return (
            self.python_package_generator
            .generate_package(
                client_code=
                    client_code,

                request_model=
                    models[
                        "request"
                    ],

                response_model=
                    models[
                        "response"
                    ]
            )
        )



