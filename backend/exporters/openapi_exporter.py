import importlib
import json
from pathlib import Path

_YAML_RESERVED_WORDS = {"true", "false", "null", "~", "yes", "no", "on", "off"}


def _needs_yaml_quoting(text):
    if text == "" or text != text.strip():
        return True
    if text.lower() in _YAML_RESERVED_WORDS:
        return True
    if text[0] in "!&*-?:,[]{}#|>'\"%@`":
        return True
    if ": " in text or text.endswith(":") or " #" in text:
        return True
    try:
        float(text)
        return True
    except ValueError:
        return False


def _yaml_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # json.dumps produces a valid YAML double-quoted scalar (JSON string
    # syntax is a subset of YAML's), so it's reused here instead of
    # hand-rolling escaping for quotes/colons/backslashes.
    return json.dumps(text) if _needs_yaml_quoting(text) else text


def _to_yaml(value, indent=0):
    """A minimal, dependency-free YAML serializer for the plain dict/list/
    scalar data an OpenAPI schema (app.openapi()) is made of -- same
    technique already used by the export_service.py modules across
    backend/{ai,security,cluster,storage,observability}, but with scalar
    quoting so URLs and descriptions containing ':' (extremely common in
    an OpenAPI schema) don't produce invalid/ambiguous YAML.
    """
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return f"{pad}{{}}\n"
        lines = []
        for key, val in value.items():
            key_str = _yaml_scalar(key) if isinstance(key, str) else str(key)
            # An *empty* dict/list must still render as an inline "{}"/"[]"
            # mapping/sequence, not fall through to _yaml_scalar (which
            # would stringify it as the text "{}" -- a YAML string, not an
            # empty mapping). OpenAPI schemas are full of these (e.g. an
            # unconstrained `"schema": {}`), so this isn't an edge case.
            if isinstance(val, dict):
                if val:
                    lines.append(f"{pad}{key_str}:")
                    lines.append(_to_yaml(val, indent + 1).rstrip("\n"))
                else:
                    lines.append(f"{pad}{key_str}: {{}}")
            elif isinstance(val, list):
                if val:
                    lines.append(f"{pad}{key_str}:")
                    lines.append(_to_yaml(val, indent).rstrip("\n"))
                else:
                    lines.append(f"{pad}{key_str}: []")
            else:
                lines.append(f"{pad}{key_str}: {_yaml_scalar(val)}")
        return "\n".join(lines) + "\n"
    if isinstance(value, list):
        if not value:
            return f"{pad}[]\n"
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                item_lines = _to_yaml(item, indent + 1).rstrip("\n").split("\n")
                lines.append(f"{pad}- {item_lines[0].strip()}")
                lines.extend(item_lines[1:])
            elif isinstance(item, list) and item:
                item_lines = _to_yaml(item, indent + 1).rstrip("\n").split("\n")
                lines.append(f"{pad}- {item_lines[0].strip()}")
                lines.extend(item_lines[1:])
            elif isinstance(item, dict):
                lines.append(f"{pad}- {{}}")
            elif isinstance(item, list):
                lines.append(f"{pad}- []")
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines) + "\n"
    return f"{pad}{_yaml_scalar(value)}\n"


def export_openapi_schema(output_path="generated/openapi.json", package_name="generated", format="json"):
    """Export the FastAPI OpenAPI schema to a JSON or YAML file.

    Parameters
    ----------
    output_path: str, optional
        Destination path for the OpenAPI schema file. Defaults to
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
    format: str, optional
        Either "json" (default) or "yaml". YAML is the format most
        OpenAPI tooling (Swagger UI configs, API gateways, codegen)
        actually expects; JSON-only export was the only option before.
    """
    module = importlib.import_module(f"{package_name}.app")
    app = module.app

    schema = app.openapi()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        if format == "yaml":
            f.write(_to_yaml(schema))
        else:
            json.dump(schema, f, indent=2)

    print(f"OpenAPI schema written to {output_path}")
