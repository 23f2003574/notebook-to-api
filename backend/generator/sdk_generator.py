from pathlib import Path


class SDKGenerator:
    """
    Generates SDK clients from analyzed notebook functions.
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def generate(
        self,
        functions,
        language: str = "python"
    ):
        if language == "python":
            return self._generate_python_sdk(functions)

        raise ValueError(
            f"Unsupported SDK language: {language}"
        )

    def _generate_python_sdk(
        self,
        functions
    ):
        sdk_dir = self.output_dir / "python_sdk"

        sdk_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        init_file = sdk_dir / "__init__.py"

        init_file.write_text(
            "from .client import APIClient\n"
        )

        client_file = sdk_dir / "client.py"

        client_file.write_text(
            self._build_client_template()
        )

        return sdk_dir

    def _build_client_template(self):
        return """
import requests


class APIClient:
    def __init__(
        self,
        base_url,
        api_key=None
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
"""