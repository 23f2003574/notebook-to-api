from dataclasses import dataclass


@dataclass
class TestCase:

    name: str

    method: str

    expected_status: int


class TestCaseEngine:

    def generate(
        self
    ):

        return [

            TestCase(

                name=
                    "health_check",

                method=
                    "GET",

                expected_status=
                    200
            ),

            TestCase(

                name=
                    "invalid_request",

                method=
                    "POST",

                expected_status=
                    400
            ),

            TestCase(

                name=
                    "unauthorized_request",

                method=
                    "GET",

                expected_status=
                    401
            )
        ]
