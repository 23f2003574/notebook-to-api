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
        raise NotImplementedError(
            "Python SDK generation not implemented yet."
        )