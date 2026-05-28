import json
from pathlib import Path


def _method_name_from_path(path: str) -> str:
    """Convert an API path like '/train_model' to a pythonic method name 'train_model'."""
    return path.lstrip('/').replace('-', '_')


def generate_python_sdk(
    openapi_path: str = "generated/openapi.json",
    output_path: str = "generated/sdk/python_client.py",
):
    """Generate a minimal Python SDK client from a FastAPI OpenAPI schema.

    The generated client contains a ``NotebookAPIClient`` class with a method for each
    POST endpoint defined in the OpenAPI spec. Each method performs a ``requests.post``
    call to the corresponding endpoint and returns ``response.json()``.
    """
    # Load OpenAPI schema
    with open(openapi_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    paths = schema.get("paths", {})
    # Prepare client code lines
    lines = []
    lines.append("import requests")
    lines.append("")
    lines.append("class NotebookAPIClient:")
    lines.append("    def __init__(self, base_url: str):")
    lines.append("        self.base_url = base_url.rstrip('/')")
    lines.append("")
    for path, methods in paths.items():
        # Only generate for POST methods (typical for notebook functions)
        post_op = methods.get("post")
        if not post_op:
            continue
        method_name = _method_name_from_path(path)
        # Determine parameter schema (simple request body expecting JSON)
        lines.append(f"    def {method_name}(self, payload: dict):")
        lines.append(f"        """Call the `{path}` endpoint with JSON payload.""")
        lines.append(f"        response = requests.post(f"{self.base_url}{path}", json=payload)")
        lines.append("        response.raise_for_status()")
        lines.append("        return response.json()")
        lines.append("")
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Python SDK generated at {output_path}")
