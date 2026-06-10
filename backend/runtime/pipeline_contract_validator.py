class PipelineContractError(
    Exception
):
    pass


class PipelineContractValidator:

    def validate_outputs(
        self,
        runtime,
        expected_outputs
    ):

        missing = []

        for output_name in (
            expected_outputs
        ):

            if not runtime.has_value(
                output_name
            ):

                missing.append(
                    output_name
                )

        if missing:

            raise PipelineContractError(
                "Missing pipeline outputs: "
                + ", ".join(
                    missing
                )
            )