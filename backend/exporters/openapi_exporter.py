import importlib
import json
from pathlib import Path


def export_openapi_schema(output_path="generated/openapi.json", package_name="generated"):
    """Export the FastAPI OpenAPI schema to a JSON file.

    Parameters
    ----------
    output_path: str, optional
        Destination path for the OpenAPI JSON file. Defaults to
        ``generated/openapi.json``.
    package_name: str, optional
        The package the compiled app was written to (see
        backend.compiler.package_name_for_output_dir). Must match
        whatever --output directory was used to compile the notebook, or
        this silently imports whatever "generated.app" *does* happen to
        already be importable elsewhere on sys.path -- confirmed: with a
        custom --output, this previously exported the schema for a stale,
        unrelated, already-imported app instead of the one just compiled.

        Imported dynamically (not at module load time) both so a custom
        package_name can be honored, and to preserve the existing lazy-
        import behavior this module already relied on (see the comment in
        cli.py about not re-executing a compiled notebook's top-level
        code as a side effect of unrelated commands).
    """
    module = importlib.import_module(f"{package_name}.app")
    app = module.app

    schema = app.openapi()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    print(f"OpenAPI schema written to {output_path}")
