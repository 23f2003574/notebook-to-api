from textwrap import dedent


class PythonPackagingGenerator:

    def generate_pyproject(
        self,
        package_name: str
    ):

        return dedent(
            f"""
            [build-system]

            requires = [
                "setuptools>=61.0"
            ]

            build-backend =
                "setuptools.build_meta"

            [project]

            name =
                "{package_name}"

            version =
                "1.0.0"

            dependencies = [
                "requests",
                "pydantic"
            ]
            """
        )

    def generate_requirements(
        self
    ):

        return dedent(
            """
            requests>=2.0.0

            pydantic>=2.0.0

            httpx>=0.25.0
            """
        )
