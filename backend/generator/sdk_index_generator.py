from textwrap import dedent


class SDKIndexGenerator:

    def generate_index(
        self,
        module_names
    ):

        exports = []

        for module_name in (
            module_names
        ):

            exports.append(
                f'export * from "./{module_name}";'
            )

        return dedent(
            "\n".join(
                exports
            )
        )
