import json
import subprocess
import sys
from pathlib import Path

from backend.exporters.openapi_exporter import _to_yaml, export_openapi_schema

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_to_yaml_renders_nested_mapping():

    text = _to_yaml({"info": {"title": "My API", "version": "1.0.0"}})

    assert text == "info:\n  title: My API\n  version: 1.0.0\n"


def test_to_yaml_renders_list_of_mappings():
    """A colon not followed by whitespace (e.g. inside a URL) is
    unambiguous, valid, unquoted YAML -- only ": " or a trailing ":"
    needs quoting (see test_to_yaml_quotes_strings_containing_a_colon).
    """

    text = _to_yaml({"servers": [{"url": "http://localhost:8000"}]})

    assert "servers:" in text
    assert "- url: http://localhost:8000" in text


def test_to_yaml_quotes_strings_containing_a_colon():
    """Confirmed exploitable before quoting was added: an unquoted value
    containing ": " (extremely common in OpenAPI descriptions/URLs) is
    ambiguous/invalid YAML -- it reads as another nested mapping key.
    """

    text = _to_yaml({"description": "Returns: a result"})

    assert text == 'description: "Returns: a result"\n'


def test_to_yaml_quotes_number_looking_strings():
    """A bare `200` as a mapping key/value is read back as an int by any
    YAML parser, but OpenAPI's response-code keys are strings ("200",
    not 200) -- must stay quoted so the round-tripped type matches.
    """

    text = _to_yaml({"200": {"description": "OK"}})

    assert text.startswith('"200":\n')


def test_to_yaml_renders_empty_dict_as_inline_mapping_not_a_string():
    """Confirmed exploitable before this fix: an empty dict value fell
    through to _yaml_scalar, which stringified it as the *text* "{}"
    (a quoted YAML string) instead of an actual empty mapping -- silently
    changing the value's type. OpenAPI schemas contain many of these
    (e.g. an unconstrained `"schema": {}`).
    """

    text = _to_yaml({"schema": {}})

    assert text == "schema: {}\n"
    assert '"{}"' not in text


def test_to_yaml_renders_empty_list_as_inline_sequence_not_a_string():

    text = _to_yaml({"tags": []})

    assert text == "tags: []\n"


def test_export_openapi_schema_writes_valid_yaml_end_to_end(tmp_path):
    """Full pipeline: compile a real notebook, export its OpenAPI schema
    as YAML, and confirm the file is well-formed enough that indentation
    is consistent and every declared notebook endpoint actually appears
    (rather than just checking the function doesn't crash).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook, package_name_for_output_dir
from backend.exporters.openapi_exporter import export_openapi_schema

compile_notebook({str(notebook_path)!r}, "generated")
export_openapi_schema(
    "generated/openapi.yaml",
    package_name_for_output_dir("generated"),
    format="yaml",
)
print("YAML_EXPORT_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "YAML_EXPORT_OK" in proc.stdout

    yaml_text = (workdir / "generated" / "openapi.yaml").read_text(encoding="utf-8")

    assert "openapi:" in yaml_text
    assert "/add:" in yaml_text
    # Every line is either blank or starts with an even number of spaces
    # (this serializer only ever emits 2-space indents).
    for line in yaml_text.splitlines():
        if not line.strip():
            continue
        leading_spaces = len(line) - len(line.lstrip(" "))
        assert leading_spaces % 2 == 0, line


def test_export_openapi_schema_still_writes_json_by_default(tmp_path):
    """format="json" (the default) must be unaffected by adding YAML
    support -- same output as before this feature existed.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": "def add(a: int, b: int) -> int:\n    return a + b\n",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook, package_name_for_output_dir
from backend.exporters.openapi_exporter import export_openapi_schema

compile_notebook({str(notebook_path)!r}, "generated")
export_openapi_schema("generated/openapi.json", package_name_for_output_dir("generated"))
print("JSON_EXPORT_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "JSON_EXPORT_OK" in proc.stdout

    schema = json.loads((workdir / "generated" / "openapi.json").read_text(encoding="utf-8"))
    assert "/add" in schema["paths"]
