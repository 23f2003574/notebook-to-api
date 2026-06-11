from textwrap import dedent


class TypeScriptInterfaceGenerator:

    TYPE_MAPPING = {
        "str": "string",
        "int": "number",
        "float": "number",
        "bool": "boolean"
    }

    def map_type(
        self,
        python_type: str
    ):

        return self.TYPE_MAPPING.get(
            python_type,
            "any"
        )

    def generate_interface(
        self,
        interface_name: str,
        fields: dict
    ):

        field_lines = []

        for (
            field_name,
            field_type
        ) in fields.items():

            ts_type = (
                self.map_type(
                    field_type
                )
            )

            field_lines.append(
                f"{field_name}: {ts_type};"
            )

        field_block = "\n".join(
            field_lines
        )

        return dedent(
            f"""
            export interface {interface_name} {{
            {field_block}
            }}
            """
        )
