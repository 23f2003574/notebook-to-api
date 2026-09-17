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


def test_to_yaml_quotes_a_string_containing_an_embedded_newline():
    """Confirmed exploitable before this fix: an unquoted scalar
    containing a literal "\\n" is written out with an actual line break
    in the file, not an escaped one -- "description: line1\\nline2"
    became two lines, the second ("line2") reading as a bare, keyless
    entry instead of a continuation of the first line's value. Entirely
    realistic content, not a theoretical edge case: a notebook parameter
    default like `def f(text: str = "line1\\nline2")` flows straight into
    this same scalar's example value in the exported schema.
    """

    text = _to_yaml({"description": "line1\nline2"})

    assert text == 'description: "line1\\nline2"\n'
    # Exactly one line in the *file* for this mapping entry -- the
    # newline must be escaped text inside the quotes, not an actual line
    # break splitting the entry in two.
    assert text.count("\n") == 1


def test_to_yaml_quotes_a_string_containing_a_tab_or_carriage_return():

    assert _to_yaml({"x": "a\tb"}) == 'x: "a\\tb"\n'
    assert _to_yaml({"x": "a\rb"}) == 'x: "a\\rb"\n'


def test_to_yaml_quotes_a_date_looking_string():
    """Confirmed exploitable before this fix: YAML 1.1's own implicit
    "timestamp" resolver (applied by PyYAML's SafeLoader, and in practice
    by most other YAML 1.1 parsers a generated openapi.yaml might
    actually be read by) reinterprets an unquoted "2024-01-01" as a real
    date object on load, not the string this tool's own JSON export
    (openapi.json, which has no such implicit typing) already reports
    for the identical value -- entirely realistic content, not a
    theoretical edge case: a notebook parameter/example value of type
    ``date`` is exactly this shape.
    """

    text = _to_yaml({"example": "2024-01-01"})

    assert text == 'example: "2024-01-01"\n'


def test_to_yaml_quotes_a_full_iso_timestamp_looking_string():

    assert (
        _to_yaml({"example": "2024-01-01T12:00:00Z"})
        == 'example: "2024-01-01T12:00:00Z"\n'
    )
    assert (
        _to_yaml({"example": "2024-01-01 12:00:00+05:30"})
        == 'example: "2024-01-01 12:00:00+05:30"\n'
    )


def test_to_yaml_quotes_a_sexagesimal_looking_string():
    """Confirmed exploitable before this fix: YAML 1.1's own implicit
    sexagesimal ("H:MM:SS"-shaped) int/float resolver reinterpreted an
    unquoted "12:30:00" as the plain integer 45000 (12*3600 + 30*60) on
    load -- an entirely ordinary value for a notebook parameter/example
    of type ``time``.
    """

    text = _to_yaml({"example": "12:30:00"})

    assert text == 'example: "12:30:00"\n'


def test_to_yaml_quotes_a_sexagesimal_float_looking_string():

    assert _to_yaml({"example": "1:15:30.5"}) == 'example: "1:15:30.5"\n'


def test_to_yaml_quotes_a_bare_equals_sign():
    """"=" is its own reserved YAML scalar (the "default value"/merge-key
    tag, ``tag:yaml.org,2002:value``) -- unlike the timestamp/sexagesimal
    cases above, an unquoted bare "=" doesn't silently change type, it
    raises a ConstructorError on load under PyYAML's SafeLoader.
    """

    assert _to_yaml({"example": "="}) == 'example: "="\n'


def test_to_yaml_does_not_quote_a_version_looking_string():
    """A string merely containing digits and colons/dots in some other
    shape (a semver-ish "v1.2.3", or a URL's own "http://host:8000"
    already covered by test_to_yaml_renders_list_of_mappings) must not
    be over-quoted just because it superficially resembles the
    timestamp/sexagesimal patterns above.
    """

    assert _to_yaml({"example": "v1.2.3"}) == "example: v1.2.3\n"
    assert _to_yaml({"example": "not-a-date-2024"}) == "example: not-a-date-2024\n"


def test_to_yaml_quotes_a_dot_prefixed_inf_or_nan_looking_string():
    """Confirmed exploitable before this fix: PyYAML's own implicit
    float resolver matches a dot-prefixed "[-+]?.(inf|Inf|INF)"/
    ".(nan|NaN|NAN)" the same way it matches a plain "42" -- an unquoted
    ".inf"/"-.inf"/".nan" reinterprets as a real float (inf/nan) on
    load, not the string this tool's own JSON export already reports for
    the identical value. _needs_yaml_quoting's own float(text) fallback
    doesn't catch this: float(".inf") itself raises ValueError in real
    Python (it needs a bare "inf", no leading dot).
    """

    assert _to_yaml({"example": ".inf"}) == 'example: ".inf"\n'
    assert _to_yaml({"example": "-.inf"}) == 'example: "-.inf"\n'
    assert _to_yaml({"example": "+.inf"}) == 'example: "+.inf"\n'
    assert _to_yaml({"example": ".Inf"}) == 'example: ".Inf"\n'
    assert _to_yaml({"example": ".NAN"}) == 'example: ".NAN"\n'


def test_to_yaml_does_not_over_quote_a_plus_signed_nan():
    """PyYAML's own resolver genuinely never signs "nan" -- only "inf"
    can take a "+"/"-" -- confirmed against a real yaml.safe_load
    ("+.nan"/"-.nan" both stay plain strings on load). "+.nan" (unlike
    "-.nan", whose leading "-" is independently quoted anyway as a
    reserved YAML indicator character -- see the leading-character check
    above) isolates the actual boundary this regex is built to match
    exactly: nan itself is never signed.
    """

    assert _to_yaml({"example": "+.nan"}) == "example: +.nan\n"


def test_to_yaml_quotes_a_hex_or_binary_looking_string():
    """Confirmed exploitable before this fix: PyYAML's own implicit int
    resolver matches a "0x"-prefixed hex ("[-+]?0x[0-9a-fA-F_]+") and a
    "0b"-prefixed binary ("[-+]?0b[0-1_]+") literal, the same way it
    already matches a plain "42" -- an unquoted "0x1A"/"0b101"
    reinterprets as a real int (26/5) on load. Realistic content, not a
    theoretical edge case: a hex-formatted id/opcode/color string is
    exactly this shape. float(text)'s own fallback doesn't catch this
    either: float("0x1A") raises ValueError in real Python.
    """

    assert _to_yaml({"example": "0x1A"}) == 'example: "0x1A"\n'
    assert _to_yaml({"example": "-0x1a"}) == 'example: "-0x1a"\n'
    assert _to_yaml({"example": "0b101"}) == 'example: "0b101"\n'
    assert _to_yaml({"example": "+0b101"}) == 'example: "+0b101"\n'


def test_to_yaml_does_not_over_quote_an_uppercase_prefixed_hex_or_binary():
    """PyYAML's own resolver only recognizes a lowercase "0x"/"0b" prefix
    -- "0X1A"/"0B101" are left as plain strings by a real yaml.safe_load,
    confirmed directly -- so quoting them here would be needlessly
    conservative, not wrong. Pins the actual boundary the regex above is
    built to match exactly, the same way test_to_yaml_does_not_quote_a_
    version_looking_string above already pins the timestamp/sexagesimal
    boundary.
    """

    assert _to_yaml({"example": "0X1A"}) == "example: 0X1A\n"
    assert _to_yaml({"example": "0B101"}) == "example: 0B101\n"


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


def test_export_openapi_schema_escapes_a_multiline_default_value_in_yaml(tmp_path):
    """End-to-end version of test_to_yaml_quotes_a_string_containing_an_embedded_newline:
    a notebook parameter defaulting to a multi-line string flows into
    that same parameter's example value in the generated app's OpenAPI
    schema (see example_payload in backend/parser/ast_parser.py). Before
    the underlying fix, exporting that schema as YAML produced a file
    where the second line of the default value split off into an
    invalid, keyless entry instead of staying part of the same scalar.
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
                            "def greet(text: str = 'line1\\nline2') -> str:\n"
                            "    return text\n"
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
print("MULTILINE_YAML_EXPORT_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MULTILINE_YAML_EXPORT_OK" in proc.stdout

    yaml_text = (workdir / "generated" / "openapi.yaml").read_text(encoding="utf-8")

    # The escaped form must be present as literal backslash-n text inside
    # a quoted scalar -- not an actual line break splitting "line2" onto
    # its own, keyless line.
    assert "line1\\nline2" in yaml_text
    assert not any(
        line.strip() == "line2" for line in yaml_text.splitlines()
    )
    # Every line is either blank or starts with an even number of spaces
    # (this serializer only ever emits 2-space indents) -- a stray
    # keyless "line2" entry would violate that too.
    for line in yaml_text.splitlines():
        if not line.strip():
            continue
        leading_spaces = len(line) - len(line.lstrip(" "))
        assert leading_spaces % 2 == 0, line


def test_export_openapi_schema_quotes_a_date_looking_default_value_in_yaml(tmp_path):
    """End-to-end version of test_to_yaml_quotes_a_date_looking_string: a
    notebook parameter defaulting to a date-shaped string flows into
    that same parameter's example value in the generated app's OpenAPI
    schema (see example_payload in backend/parser/ast_parser.py). Before
    the underlying fix, exporting that schema as YAML left the value
    unquoted -- YAML 1.1's own implicit "timestamp" resolver
    reinterprets it as a real date object on load, not the string this
    tool's own JSON export already reports for the identical value.
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
                            "def log_event(when: str = '2024-01-01') -> str:\n"
                            "    return when\n"
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
print("DATE_YAML_EXPORT_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DATE_YAML_EXPORT_OK" in proc.stdout

    yaml_text = (workdir / "generated" / "openapi.yaml").read_text(encoding="utf-8")

    assert '"2024-01-01"' in yaml_text
    assert "when: 2024-01-01\n" not in yaml_text


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
