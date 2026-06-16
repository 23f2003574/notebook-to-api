from dataclasses import dataclass


@dataclass
class APIUsageExample:

    endpoint: str

    request_example: dict

    response_example: dict


class APIUsageExampleGenerator:

    def generate(
        self,
        endpoint
    ):

        request = {

            parameter:
                "example"

            for parameter
            in endpoint.parameters
        }

        response = {

            "result":
                "success"
        }

        return APIUsageExample(

            endpoint=
                endpoint.path,

            request_example=
                request,

            response_example=
                response
        )
