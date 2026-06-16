from dataclasses import dataclass


@dataclass
class APIErrorDocumentation:

    status_code: int

    error_name: str

    description: str

    resolution: str


class APIErrorDocumentationGenerator:

    DEFAULT_ERRORS = [

        (
            400,
            "Bad Request",
            "Request payload invalid",
            "Verify request parameters"
        ),

        (
            422,
            "Validation Error",
            "Input validation failed",
            "Check required fields"
        ),

        (
            500,
            "Internal Server Error",
            "Unexpected server failure",
            "Review server logs"
        )
    ]

    def generate(
        self
    ):

        errors = []

        for (
            status_code,
            error_name,
            description,
            resolution
        ) in self.DEFAULT_ERRORS:

            errors.append(

                APIErrorDocumentation(

                    status_code=
                        status_code,

                    error_name=
                        error_name,

                    description=
                        description,

                    resolution=
                        resolution
                )
            )

        return errors
