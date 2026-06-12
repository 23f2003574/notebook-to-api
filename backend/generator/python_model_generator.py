from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PythonModelGenerator:

    TYPE_MAPPING = {
        "str": "str",
        "int": "int",
        "float": "float",
        "bool": "bool"
    }

    def generate_model(
        self,
        model_name: str,
        fields: dict
    ):

        field_lines = []

        for (
            field_name,
            field_type
        ) in fields.items():

            mapped_type = (
                self.TYPE_MAPPING.get(
                    field_type,
                    "str"
                )
            )

            field_lines.append(
                f"{field_name}: {mapped_type}"
            )

        field_block = "\n    ".join(
            field_lines
        )

        return dedent(
            f"""
            from pydantic import BaseModel


            class {model_name}(
                BaseModel
            ):

                {field_block}
            """
        )
