from dataclasses import dataclass


@dataclass
class IntegrationTest:

    name: str

    component: str

    expected_result: str


class IntegrationTestEngine:

    def generate(
        self
    ):

        return [

            IntegrationTest(

                name=
                    "database_connectivity",

                component=
                    "database",

                expected_result=
                    "connection_successful"
            ),

            IntegrationTest(

                name=
                    "external_api_connectivity",

                component=
                    "external_api",

                expected_result=
                    "response_received"
            ),

            IntegrationTest(

                name=
                    "end_to_end_request",

                component=
                    "full_stack",

                expected_result=
                    "request_completed"
            )
        ]
