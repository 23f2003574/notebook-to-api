from textwrap import dedent

from backend.analyzer.pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


class PythonDocsGenerator:

    def generate_readme(
        self,
        spec: PipelineEndpointSpec
    ):

        return dedent(
            f"""
            # {spec.python_package_name()}

            ## Installation

            pip install {spec.python_package_name()}

            ## Usage

            ```python
            from {
                spec.python_package_name()
            } import (
                {spec.python_client_name()}
            )

            client = (
                {spec.python_client_name()}(
                    base_url=
                        "http://localhost:8000"
                )
            )

            result = (
                client.{spec.client_method_name()}(
                    payload={{}}
                )
            )
            ```

            ## Endpoint

            POST /{spec.endpoint_name}
            """
        )
