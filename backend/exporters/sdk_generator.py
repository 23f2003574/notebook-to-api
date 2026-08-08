import json
import re
from pathlib import Path


def _method_name_from_path(path: str) -> str:
    """Convert an API path into a valid Python identifier.

    Notebook-derived endpoints are always single-segment (e.g.
    '/train_model'), but a real compiled app also exposes built-in
    multi-segment paths such as '/tasks/cleanup' and '/tasks/reset'. A
    naive `lstrip('/').replace('-', '_')` leaves the '/' in place, which
    produces an invalid method definition like `def tasks/cleanup(...)`.
    Replacing every run of non-identifier characters (slashes, hyphens,
    path-parameter braces) with a single underscore keeps names readable
    and always syntactically valid.
    """
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", path.strip("/")).strip("_")

    if not name:
        name = "root"
    elif name[0].isdigit():
        name = f"_{name}"

    return name


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
    lines.append("import os")
    lines.append("import requests")
    lines.append("")
    lines.append("class NotebookAPIClient:")
    lines.append("    def __init__(self, base_url: str, api_key: str = None):")
    lines.append("        self.base_url = base_url.rstrip('/')")
    lines.append("        # Generated endpoints require the same X-API-Key header the")
    lines.append("        # generated app itself defaults to (see NOTEBOOK_API_KEY in")
    lines.append("        # api_generator.py) so the client works out of the box locally.")
    lines.append("        self.api_key = api_key or os.getenv(")
    lines.append("            'NOTEBOOK_API_KEY', 'notebook-to-api-dev-key'")
    lines.append("        )")
    lines.append("")
    for path, methods in paths.items():
        # Only generate for POST methods (typical for notebook functions)
        post_op = methods.get("post")
        if not post_op:
            continue
        method_name = _method_name_from_path(path)
        # Determine parameter schema (simple request body expecting JSON)
        lines.append(f"    def {method_name}(self, payload: dict):")
        lines.append(f'        """Call the `{path}` endpoint with JSON payload."""')
        lines.append(f'        response = requests.post(')
        lines.append(f'            f"{{self.base_url}}{path}",')
        lines.append(f'            json=payload,')
        lines.append(f'            headers={{"X-API-Key": self.api_key}},')
        lines.append(f'        )')
        lines.append("        response.raise_for_status()")
        lines.append("        return response.json()")
        lines.append("")
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Python SDK generated at {output_path}")
