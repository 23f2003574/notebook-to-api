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


@dataclass
class APIExample:

    endpoint_name: str

    request_example: dict

    response_example: dict


class APIExampleEngine:

    def generate(
        self,
        endpoint_name,
        request_schema,
        response_schema
    ):

        request_example = {}

        for field in (
            request_schema.fields
        ):

            request_example[
                field.name
            ] = (
                f"sample_{field.field_type}"
            )

        response_example = {}

        for field in (
            response_schema.fields
        ):

            response_example[
                field.name
            ] = (
                f"sample_{field.field_type}"
            )

        return APIExample(

            endpoint_name=
                endpoint_name,

            request_example=
                request_example,

            response_example=
                response_example
        )
