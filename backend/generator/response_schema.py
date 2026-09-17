from dataclasses import dataclass


@dataclass
class ResponseField:

    name: str

    field_type: str


@dataclass
class ResponseSchema:

    title: str

    fields: list[ResponseField]


class ResponseSchemaEngine:

    def generate(
        self,
        outputs
    ):

        fields = []

        for output in outputs:

            fields.append(

                ResponseField(

                    name=
                        output.output_type,

                    field_type=
                        "string"
                )
            )

        return ResponseSchema(

            title=
                "GeneratedResponse",

            fields=
                fields
        )
