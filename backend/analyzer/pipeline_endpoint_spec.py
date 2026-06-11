from dataclasses import dataclass
from typing import List


@dataclass
class PipelineEndpointSpec:

    endpoint_name: str

    input_fields: List[str]

    output_fields: List[str]

    execution_stages: int

    parallelism_score: float

    def route_name(
        self
    ):

        return (
            self.endpoint_name
            .replace(
                "-",
                "_"
            )
        )

    def request_model_name(
        self
    ):

        return (
            self.route_name()
            .title()
            .replace(
                "_",
                ""
            )
            + "Request"
        )

    def response_model_name(
        self
    ):

        return (
            self.route_name()
            .title()
            .replace(
                "_",
                ""
            )
            + "Response"
        )

    def typescript_request_name(
        self
    ):

        return (
            self.request_model_name()
        )

    def typescript_response_name(
        self
    ):

        return (
            self.response_model_name()
        )

    def client_method_name(
        self
    ):

        return self.route_name()

    def sdk_module_name(
        self
    ):

        return (
            self.route_name()
            + "_sdk"
        )

    def sdk_filename(
        self
    ):

        return (
            self.sdk_module_name()
            + ".ts"
        )

    def metadata_name(
        self
    ):

        return (
            self.route_name()
            .title()
            .replace(
                "_",
                ""
            )
            + "Metadata"
        )

    def execution_summary(
        self
    ):

        return {
            "endpoint":
                self.endpoint_name,

            "inputs":
                self.input_fields,

            "outputs":
                self.output_fields,

            "execution_stages":
                self.execution_stages,

            "parallelism_score":
                self.parallelism_score
        }