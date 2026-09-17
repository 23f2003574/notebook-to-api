from dataclasses import dataclass


@dataclass
class SDKMethod:

    method_name: str

    endpoint_name: str

    request_fields: list[str]


class SDKMethodGenerator:

    def generate(
        self,
        endpoint_name,
        request_schema
    ):

        fields = [

            field.name

            for field

            in request_schema.fields
        ]

        return SDKMethod(

            method_name=
                endpoint_name,

            endpoint_name=
                endpoint_name,

            request_fields=
                fields
        )
