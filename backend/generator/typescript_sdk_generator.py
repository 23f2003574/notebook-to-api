from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)

from .typescript_interface_generator import (
    TypeScriptInterfaceGenerator
)

from .typescript_client_generator import (
    TypeScriptClientGenerator
)


class TypeScriptSDKGenerator:

    def __init__(
        self
    ):

        self.interface_generator = (
            TypeScriptInterfaceGenerator()
        )

        self.client_generator = (
            TypeScriptClientGenerator()
        )

    def generate_sdk(
        self,
        spec: PipelineEndpointSpec,
        sdk_types: dict
    ):

        request_interface = (
            self.interface_generator
            .generate_interface(
                spec.request_model_name(),
                sdk_types[
                    "request_types"
                ]
            )
        )

        response_interface = (
            self.interface_generator
            .generate_interface(
                spec.response_model_name(),
                sdk_types[
                    "response_types"
                ]
            )
        )

        client_method = (
            self.client_generator
            .generate_method(
                spec
            )
        )

        return dedent(
            f"""
            {request_interface}

            {response_interface}

            {client_method}
            """
        )
