from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PipelineContractValidator:

    def validate_schema(
        self,
        spec: PipelineEndpointSpec,
        schema: dict
    ):

        request_fields = set(
            schema[
                "request"
            ].keys()
        )

        response_fields = set(
            schema[
                "response"
            ].keys()
        )

        expected_inputs = set(
            spec.input_fields
        )

        expected_outputs = set(
            spec.output_fields
        )

        if (
            request_fields
            != expected_inputs
        ):

            raise ValueError(
                "Request schema does not "
                "match endpoint spec"
            )

        if (
            response_fields
            != expected_outputs
        ):

            raise ValueError(
                "Response schema does not "
                "match endpoint spec"
            )

        return True
