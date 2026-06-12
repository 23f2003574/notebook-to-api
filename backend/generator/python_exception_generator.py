from textwrap import dedent


class PythonExceptionGenerator:

    def generate_exceptions(
        self
    ):

        return dedent(
            """
            class SDKError(
                Exception
            ):
                pass


            class APIError(
                SDKError
            ):

                def __init__(
                    self,
                    status_code,
                    message
                ):

                    self.status_code = (
                        status_code
                    )

                    super().__init__(
                        message
                    )


            class ValidationError(
                SDKError
            ):
                pass


            class RetryError(
                SDKError
            ):
                pass
            """
        )

