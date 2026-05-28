import json
from generated.app import app


def export_openapi_schema(output_path="generated/openapi.json"):
    """Export the FastAPI OpenAPI schema to a JSON file.

    Parameters
    ----------
    output_path: str, optional
        Destination path for the OpenAPI JSON file. Defaults to
        ``generated/openapi.json``.
    """
    schema = app.openapi()
    # Ensure the output directory exists
    from pathlib import Path
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
    print(f"OpenAPI schema written to {output_path}")
