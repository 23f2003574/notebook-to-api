import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_notebook(path):
    path.write_text(
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


def _write_notebook_with_function(path, function_source):
    path.write_text(
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
                        "source": function_source,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _run_cli(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_compile_command_writes_the_generated_app(tmp_path):
    """The `compile`, `inspect`, `export-openapi`, and `export-sdk`
    subcommands were previously only exercised by calling their
    underlying functions directly (see test_compiler.py,
    test_openapi_exporter.py, test_sdk_generator.py) -- never through the
    actual `backend.cli` argparse entry point, unlike `deploy`
    (test_cli_deploy.py). That left the subparser wiring itself
    untested: test_deploy_command_is_registered in test_cli_deploy.py
    documents a real bug this exact gap already let through once (a
    dispatch branch in main() with no matching add_parser(...)).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "built" / "app.py").exists()
    assert (workdir / "built" / "requirements.txt").exists()
    assert (workdir / "built" / "Dockerfile").exists()
    assert "Compilation finished" in proc.stdout


def test_compile_command_defaults_output_to_generated(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(["compile", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "generated" / "app.py").exists()


def _write_add_subtract_notebook(path):
    _write_notebook_with_function(
        path,
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a - b\n",
    )


def test_compile_command_only_compiles_just_the_named_function(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_add_subtract_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built", "--only", "add"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    generated_app = (workdir / "built" / "app.py").read_text(encoding="utf-8")
    assert '"/add"' in generated_app
    assert '"/subtract"' not in generated_app
    assert "Generated 1 endpoint(s):" in proc.stdout
    assert "POST /add" in proc.stdout
    assert "POST /subtract" not in proc.stdout


def test_compile_command_exclude_omits_just_the_named_function(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_add_subtract_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built", "--exclude", "subtract"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    generated_app = (workdir / "built" / "app.py").read_text(encoding="utf-8")
    assert '"/add"' in generated_app
    assert '"/subtract"' not in generated_app


def test_compile_command_only_accepts_a_comma_separated_list(tmp_path):

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
                            "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                            "def subtract(a: int, b: int) -> int:\n    return a - b\n\n"
                            "def multiply(a: int, b: int) -> int:\n    return a * b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        [
            "compile", str(notebook_path), "--output", "built",
            "--only", "add, multiply",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    generated_app = (workdir / "built" / "app.py").read_text(encoding="utf-8")
    assert '"/add"' in generated_app
    assert '"/multiply"' in generated_app
    assert '"/subtract"' not in generated_app


def test_compile_command_only_and_exclude_together_reports_a_clean_error(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_add_subtract_notebook(notebook_path)

    proc = _run_cli(
        [
            "compile", str(notebook_path), "--output", "built",
            "--only", "add", "--exclude", "subtract",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "can't both be given")


def test_compile_command_only_reports_a_clean_error_for_an_unknown_function(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_add_subtract_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built", "--only", "nope"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "not defined in this notebook")


def test_compile_command_json_with_only_reflects_just_the_compiled_functions(tmp_path):
    """compile --json's own output must not claim an endpoint exists for a
    function --only just excluded from the actual compile -- confirmed
    this would otherwise happen, since inspect_notebook_data re-parses the
    notebook fresh with no idea --only/--exclude restricted anything.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_add_subtract_notebook(notebook_path)

    proc = _run_cli(
        [
            "compile", str(notebook_path), "--output", "built",
            "--only", "add", "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    assert [f["name"] for f in data["functions"]] == ["add"]
    assert [ep["path"] for ep in data["endpoints"]] == ["/add"]


def test_compile_command_prints_a_summary_of_generated_endpoints(tmp_path):
    """Before this, `compile` printed a single "Compilation finished"
    line and nothing else -- seeing what had actually been generated
    (the endpoint list, which ones are background/task_id-based,
    dependencies) required a separate `inspect` call, even though
    POST /api/compile's response already returns exactly this
    information.
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
                            "    return a + b\n\n"
                            "def train_model(epochs: int) -> str:\n"
                            "    return 'done'\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Generated 2 endpoint(s):" in proc.stdout
    assert "POST /add" in proc.stdout
    assert "POST /train_model  [background]" in proc.stdout
    # add itself must not be flagged as background.
    add_line = next(
        line for line in proc.stdout.splitlines() if line.strip() == "POST /add"
    )
    assert "[background]" not in add_line
    # No third-party imports in this notebook -- the "Dependencies:" line
    # is only printed when there's something to report.
    assert "Dependencies:" not in proc.stdout


def test_compile_command_summary_lists_third_party_dependencies(tmp_path):

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
                            "import pandas as pd\n\n"
                            "def summarize(count: int) -> int:\n"
                            "    return count * 2\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Dependencies: pandas" in proc.stdout


def test_compile_command_json_flag_emits_machine_readable_output(tmp_path):
    """Before --json existed on `compile`, the only way to get a
    compile's outcome (functions, dependencies, generated_files,
    endpoints, skipped_functions) as structured data was a separate
    `inspect --json` call afterwards -- `compile` itself only ever printed
    the human-readable summary (print_compile_summary), even though
    POST /api/compile's REST response already returns exactly this kind
    of data for the same operation. Reuses inspect_notebook_data (the
    same function `inspect --json` calls) so the two can't drift, now
    reflecting the app this compile call just actually produced.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built", "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # compile_notebook (backend/compiler.py) itself unconditionally prints
    # progress lines ("Starting compilation for: ...", "Runtime module
    # generated.", ...) on top of print_compile_summary's human-readable
    # summary -- none of that may leak onto stdout in --json mode, or a
    # script doing json.loads(stdout) would choke on it. The whole of
    # stdout must be nothing but the JSON document itself.
    data = json.loads(proc.stdout)
    assert data["functions"][0]["name"] == "add"
    assert "app.py" in data["generated_files"]
    assert "requirements.txt" in data["generated_files"]
    assert data["dependencies"] == []
    assert data["reserved_name_conflicts"] == []
    assert data["endpoints"] == [
        {"path": "/add", "method": "POST", "is_async": False}
    ]
    assert data["skipped_functions"] == []


def test_compile_command_json_flag_reports_a_background_endpoint(tmp_path):

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
                            "def train_model(epochs: int) -> str:\n"
                            "    return 'done'\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built", "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["endpoints"] == [
        {"path": "/train_model", "method": "POST", "is_async": True}
    ]


def test_inspect_command_reports_the_notebooks_function(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["inspect", str(notebook_path), "--output", "built"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "add(a: int, b: int) -> int" in proc.stdout
    assert "Route: POST /add" in proc.stdout


def test_inspect_command_does_not_create_the_output_directory(tmp_path):
    """`inspect` is documented as a read-only "preview what compiling this
    notebook will do" step (see its own --help), but the dispatch branch
    handling it used to unconditionally `mkdir(parents=True,
    exist_ok=True)` on --output before ever reading anything -- so it
    left an empty directory tree on disk purely as a side effect, even
    against a notebook that had never been compiled and even for a
    multi-segment --output path that didn't exist yet.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["inspect", str(notebook_path), "--output", "some/nested/output_dir"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (workdir / "some").exists()


def test_inspect_command_json_flag_does_not_create_the_output_directory(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["inspect", str(notebook_path), "--output", "built", "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["generated_files"] == []
    assert not (workdir / "built").exists()


def test_inspect_command_still_lists_generated_files_when_the_directory_already_exists(
    tmp_path
):
    """The fix for the mkdir side effect above must not regress the
    ordinary case: `inspect` after a real `compile` still reports the
    files that compile actually wrote.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        ["inspect", str(notebook_path), "--output", "built"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "app.py" in proc.stdout
    assert "requirements.txt" in proc.stdout


def test_compile_command_still_creates_the_output_directory(tmp_path):
    """Unlike `inspect`, `compile` genuinely writes output there, so its
    own mkdir (and compile_notebook_to_api's own os.makedirs, backend/
    compiler.py) must be unaffected by inspect's fix above.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "some/nested/output_dir"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "some" / "nested" / "output_dir" / "app.py").is_file()


def test_inspect_command_reports_a_functions_own_docstring(tmp_path):
    """`inspect --json` (see inspect_notebook_data) already carried a
    function's own docstring, but the plain human-readable `inspect`
    report never printed it at all.
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
                            '    """Add two numbers and return their sum."""\n'
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(["inspect", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Add two numbers and return their sum." in proc.stdout


def test_inspect_command_json_flag_emits_machine_readable_output(tmp_path):
    """Before --json existed, `inspect` only ever printed the
    human-readable report (inspect_notebook) -- inspect_notebook_data,
    which returns the same functions/dependencies/generated_files as
    structured data, was only ever wired up to the REST API
    (/api/inspect), never to the CLI. A script parsing `inspect`'s stdout
    had nothing but that free-form text report to work with.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        ["inspect", str(notebook_path), "--output", "built", "--json"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads(proc.stdout)
    assert data["functions"][0]["name"] == "add"
    assert "app.py" in data["generated_files"]
    assert "requirements.txt" in data["generated_files"]
    assert data["dependencies"] == []
    assert data["reserved_name_conflicts"] == []


def test_inspect_command_reports_a_reserved_name_conflict(tmp_path):
    """`inspect` is the CLI's own preview of what `compile` will do, but
    had no idea a function named "health_check" collides with an
    identifier the generated app itself defines (see
    RESERVED_INFRASTRUCTURE_NAMES in generator/api_generator.py) until
    `compile` actually failed on it.
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
                        "source": "def health_check() -> dict:\n    return {}\n",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    text_proc = _run_cli(["inspect", str(notebook_path)], cwd=workdir)
    assert text_proc.returncode == 0, text_proc.stdout + text_proc.stderr
    assert "Reserved Name Conflicts" in text_proc.stdout
    assert "health_check" in text_proc.stdout

    json_proc = _run_cli(["inspect", str(notebook_path), "--json"], cwd=workdir)
    assert json_proc.returncode == 0, json_proc.stdout + json_proc.stderr
    data = json.loads(json_proc.stdout)
    assert data["reserved_name_conflicts"] == ["health_check"]


def test_export_openapi_command_writes_json_schema_by_default(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    schema_path = workdir / "built" / "openapi.json"
    assert schema_path.exists()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "/add" in schema["paths"]


def test_export_openapi_command_writes_yaml_when_requested(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        [
            "export-openapi", "--app-dir", "built", "--format", "yaml",
            "--output", "built/openapi.yaml",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    yaml_path = workdir / "built" / "openapi.yaml"
    assert yaml_path.exists()
    assert "/add:" in yaml_path.read_text(encoding="utf-8")


def test_export_openapi_command_defaults_output_next_to_the_app_dir(tmp_path):
    """Confirmed broken before this fix: without an explicit --output,
    export-openapi wrote to a literal "generated/openapi.json" regardless
    of --app-dir -- so compiling into any directory other than the
    default "generated" and then exporting from it (a completely normal
    workflow) silently wrote the schema somewhere unrelated to, and
    possibly not even containing, the app it was exported from.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(["export-openapi", "--app-dir", "built"], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    schema_path = workdir / "built" / "openapi.json"
    assert schema_path.exists()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "/add" in schema["paths"]
    # Must not have fallen back to the old hardcoded default.
    assert not (workdir / "generated").exists()


def test_export_openapi_command_defaults_yaml_output_next_to_the_app_dir(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--format", "yaml"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    yaml_path = workdir / "built" / "openapi.yaml"
    assert yaml_path.exists()
    assert not (workdir / "generated").exists()


def test_export_openapi_command_rejects_invalid_format_choice(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(["export-openapi", "--format", "xml"], cwd=workdir)

    assert proc.returncode != 0
    assert "invalid choice: 'xml'" in proc.stderr


def test_export_sdk_command_writes_python_client_by_default(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(
        [
            "export-sdk", "--openapi", "built/openapi.json",
            "--output", "built/sdk/python_client.py",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    client_path = workdir / "built" / "sdk" / "python_client.py"
    assert client_path.exists()
    client_source = client_path.read_text(encoding="utf-8")
    assert "class NotebookAPIClient" in client_source
    assert "def add(" in client_source


def test_export_sdk_command_writes_typescript_client_when_requested(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(
        [
            "export-sdk", "--openapi", "built/openapi.json", "--language", "typescript",
            "--output", "built/sdk/typescript_client.ts",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    client_path = workdir / "built" / "sdk" / "typescript_client.ts"
    assert client_path.exists()
    assert "class NotebookAPIClient" in client_path.read_text(encoding="utf-8")


def test_export_sdk_command_defaults_output_next_to_the_openapi_file(tmp_path):
    """Confirmed broken before this fix: without an explicit --output,
    export-sdk wrote to a literal "generated/sdk/python_client.py"
    regardless of where --openapi actually pointed -- so exporting an SDK
    from a schema compiled/exported anywhere other than the default
    "generated" (a completely normal workflow) silently wrote the client
    somewhere unrelated to the schema it was generated from.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(
        ["export-sdk", "--openapi", "built/openapi.json"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    client_path = workdir / "built" / "sdk" / "python_client.py"
    assert client_path.exists()
    assert "def add(" in client_path.read_text(encoding="utf-8")
    # Must not have fallen back to the old hardcoded default.
    assert not (workdir / "generated").exists()


def test_export_sdk_command_defaults_typescript_output_next_to_the_openapi_file(
    tmp_path
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(
        ["export-sdk", "--openapi", "built/openapi.json", "--language", "typescript"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    client_path = workdir / "built" / "sdk" / "typescript_client.ts"
    assert client_path.exists()
    assert not (workdir / "generated").exists()


def test_export_sdk_command_app_dir_locates_the_matching_openapi_export(tmp_path):
    """Confirmed broken before this fix: --openapi's default was a flat
    "generated/openapi.json" literal with no --app-dir concept at all --
    unlike export-openapi, which already derives its own default --output
    from --app-dir. `export-sdk --app-dir built` (mirroring `compile
    --output built` + `export-openapi --app-dir built`, an entirely
    normal non-default workflow) crashed looking for a nonexistent
    generated/openapi.json instead of finding built/openapi.json, the
    schema that command's own prerequisite step just wrote.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(["export-openapi", "--app-dir", "built"], cwd=workdir)
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(["export-sdk", "--app-dir", "built"], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    client_path = workdir / "built" / "sdk" / "python_client.py"
    assert client_path.exists()
    assert "def add(" in client_path.read_text(encoding="utf-8")


def test_export_sdk_command_does_not_silently_use_a_stale_default_app_dir_export(
    tmp_path
):
    """The worse failure mode this fix closes: without --app-dir/--openapi
    pointing export-sdk at the right schema, it fell back to a flat
    "generated/openapi.json" literal -- so if an *unrelated* notebook had
    ever been compiled into the default "generated" directory and
    exported there too, export-sdk silently generated a client for that
    stale, unrelated schema instead, with no error or warning at all.
    Confirmed reproduced: compiling two different notebooks into "built"
    and "generated" respectively and exporting both schemas, then running
    `export-sdk --app-dir built` produced a client exposing "add" (the
    "built" notebook actually being worked with), not "multiply" (the
    unrelated notebook that happened to be sitting in the default
    "generated" directory).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    built_notebook = workdir / "nb_add.ipynb"
    _write_notebook_with_function(
        built_notebook, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    stale_notebook = workdir / "nb_multiply.ipynb"
    _write_notebook_with_function(
        stale_notebook,
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
    )

    # The unrelated notebook compiled and exported into the *default*
    # "generated" directory first -- simulating a previous, unrelated
    # `compile`/`export-openapi` run against this same working directory.
    compile_stale = _run_cli(
        ["compile", str(stale_notebook), "--output", "generated"], cwd=workdir
    )
    assert compile_stale.returncode == 0, compile_stale.stdout + compile_stale.stderr
    openapi_stale = _run_cli(["export-openapi", "--app-dir", "generated"], cwd=workdir)
    assert openapi_stale.returncode == 0, openapi_stale.stdout + openapi_stale.stderr

    # The notebook actually being worked with now, compiled and exported
    # into a different directory.
    compile_built = _run_cli(
        ["compile", str(built_notebook), "--output", "built"], cwd=workdir
    )
    assert compile_built.returncode == 0, compile_built.stdout + compile_built.stderr
    openapi_built = _run_cli(["export-openapi", "--app-dir", "built"], cwd=workdir)
    assert openapi_built.returncode == 0, openapi_built.stdout + openapi_built.stderr

    proc = _run_cli(["export-sdk", "--app-dir", "built"], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    client_source = (workdir / "built" / "sdk" / "python_client.py").read_text(
        encoding="utf-8"
    )
    assert "def add(" in client_source
    assert "def multiply(" not in client_source


def test_export_sdk_command_rejects_invalid_language_choice(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(["export-sdk", "--language", "rust"], cwd=workdir)

    assert proc.returncode != 0
    assert "invalid choice: 'rust'" in proc.stderr


def test_export_openapi_command_json_flag_emits_machine_readable_output(tmp_path):
    """Before --json existed on `export-openapi`, the only way to get its
    outcome was reading the schema file it wrote back off disk -- the
    command itself only ever printed a single human-readable "OpenAPI
    schema written to ..." line (export_openapi_schema's own print), even
    though POST /api/export-openapi's REST response already returns the
    schema inline as structured data for the same operation.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        [
            "export-openapi", "--app-dir", "built",
            "--output", "built/openapi.json", "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # export_openapi_schema unconditionally prints its own "OpenAPI schema
    # written to ..." progress line -- none of that may leak onto stdout
    # in --json mode, or a script doing json.loads(stdout) would choke on
    # it. The whole of stdout must be nothing but the JSON document
    # itself.
    data = json.loads(proc.stdout)
    assert data["status"] == "success"
    assert data["format"] == "json"
    assert data["path"] == "built/openapi.json"
    assert "/add" in data["schema"]["paths"]


def test_export_openapi_command_json_flag_reports_yaml_content_inline(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    proc = _run_cli(
        [
            "export-openapi", "--app-dir", "built", "--format", "yaml",
            "--output", "built/openapi.yaml", "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "success"
    assert data["format"] == "yaml"
    assert "/add:" in data["content"]
    assert "schema" not in data


def test_export_sdk_command_json_flag_emits_machine_readable_output(tmp_path):
    """Same gap as `export-openapi --json` above, for `export-sdk`:
    generate_python_sdk/generate_typescript_sdk only ever print a single
    "Python SDK generated at ..."/"TypeScript SDK generated at ..." line,
    even though POST /api/export-sdk's REST response already returns the
    generated client source inline for the same operation.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(
        [
            "export-sdk", "--openapi", "built/openapi.json",
            "--output", "built/sdk/python_client.py", "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "success"
    assert data["language"] == "python"
    assert data["path"] == "built/sdk/python_client.py"
    assert "class NotebookAPIClient" in data["code"]
    assert "def add(" in data["code"]


def test_export_sdk_command_json_flag_reports_typescript_language(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    compile_proc = _run_cli(
        ["compile", str(notebook_path), "--output", "built"], cwd=workdir
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    openapi_proc = _run_cli(
        ["export-openapi", "--app-dir", "built", "--output", "built/openapi.json"],
        cwd=workdir,
    )
    assert openapi_proc.returncode == 0, openapi_proc.stdout + openapi_proc.stderr

    proc = _run_cli(
        [
            "export-sdk", "--openapi", "built/openapi.json", "--language", "typescript",
            "--output", "built/sdk/typescript_client.ts", "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "success"
    assert data["language"] == "typescript"
    assert "class NotebookAPIClient" in data["code"]


def _assert_clean_cli_error(proc, expected_message_fragment):
    """A core command's expected failure modes (missing file, invalid
    notebook, etc.) must produce a single-line "Error: ..." message on
    stderr with exit code 1 -- not a raw multi-frame Python traceback.
    Before CLI_USER_FACING_ERRORS existed, every one of these scenarios
    dumped a full traceback instead (confirmed by running each command
    directly against a missing/invalid notebook).
    """

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback (most recent call last)" not in proc.stderr
    assert expected_message_fragment in proc.stderr
    assert any(
        line.startswith("Error: ") for line in proc.stderr.splitlines()
    )


def test_compile_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["compile", str(workdir / "does-not-exist.ipynb"), "--output", "built"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_compile_command_reports_a_clean_error_for_an_invalid_package_name(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["compile", str(notebook_path), "--output", "not-a-valid-package!"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "can't be used as a Python package name")


def test_compile_command_reports_a_clean_error_for_a_non_notebook_file(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    bad_notebook = workdir / "bad.ipynb"
    bad_notebook.write_text("this is not json at all", encoding="utf-8")

    proc = _run_cli(["compile", str(bad_notebook), "--output", "built"], cwd=workdir)

    _assert_clean_cli_error(proc, "does not appear to be JSON")


def test_inspect_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["inspect", str(workdir / "does-not-exist.ipynb")], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_export_openapi_command_reports_a_clean_error_when_nothing_compiled(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(["export-openapi", "--app-dir", "nope"], cwd=workdir)

    _assert_clean_cli_error(proc, "No module named 'nope'")


def test_export_sdk_command_reports_a_clean_error_for_a_missing_openapi_file(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["export-sdk", "--openapi", "missing-openapi.json"], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_export_sdk_command_hints_at_a_yaml_export_when_the_json_default_is_missing(
    tmp_path,
):
    """Confirmed exploitable before this fix: `export-sdk --openapi
    generated/openapi.json` (its own documented default) against a
    notebook only ever exported as yaml -- via `export-openapi --format
    yaml` -- crashed with a bare FileNotFoundError ("No such file or
    directory: 'openapi.json'"), giving no indication that the export the
    caller actually ran wrote openapi.yaml right next to it. Now falls
    back to reading that sibling file, which still can't be turned into
    an SDK (export-sdk only reads JSON schemas) but surfaces
    _load_openapi_schema's own specific hint (exporters/sdk_generator.py)
    instead of the generic OS error.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "openapi.yaml").write_text(
        "openapi: 3.0.0\ninfo:\n  title: Test\n  version: '1.0'\npaths: {}\n",
        encoding="utf-8",
    )

    proc = _run_cli(
        ["export-sdk", "--openapi", "openapi.json"], cwd=workdir
    )

    _assert_clean_cli_error(proc, "This looks like a YAML export")


def test_export_sdk_command_still_reports_a_clean_missing_file_error_with_no_yaml_sibling(
    tmp_path,
):
    """The fallback above must not mask a genuinely missing export --
    with no openapi.json *or* a sibling openapi.yaml/.yml anywhere to
    fall back to, this must behave exactly as before: a clean "No such
    file or directory" error, not a confusing reference to a yaml file
    that doesn't exist either.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["export-sdk", "--openapi", "openapi.json"], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_export_sdk_command_reports_a_clean_error_for_a_corrupt_openapi_file(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "openapi.json").write_text("not valid json", encoding="utf-8")

    proc = _run_cli(
        ["export-sdk", "--openapi", "openapi.json"], cwd=workdir
    )

    _assert_clean_cli_error(proc, "Expecting value")


def test_serve_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["serve", str(workdir / "does-not-exist.ipynb")], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_watch_command_is_registered():
    """`watch` (like `deploy` -- see test_deploy_command_is_registered in
    test_cli_deploy.py) needs both a subparsers.add_parser("watch", ...)
    call and a matching `elif args.command == "watch":` dispatch branch in
    _dispatch_core_command -- one without the other either makes argparse
    reject "watch" outright, or dispatches successfully into a command
    that was never actually declared. Exercised through the real
    `backend.cli` argparse entry point rather than calling
    watch_notebook directly, the same gap test_compile_command_writes_the_generated_app's
    own docstring documents for the other core commands.
    """

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "watch" in proc.stdout


def test_watch_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["watch", str(workdir / "does-not-exist.ipynb")], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_watch_command_requires_a_notebook_argument():

    proc = _run_cli(["watch"], cwd=Path.cwd())

    assert proc.returncode != 0
    assert "notebook" in proc.stderr


def test_serve_command_accepts_only_and_exclude_flags(tmp_path):
    """`serve` (and `watch`, below) previously had no --only/--exclude at
    all, unlike `compile`/`deploy` -- confirmed here by checking argparse
    itself accepts the flags (reaching the missing-notebook error, not an
    "unrecognized arguments" one), the same wiring-only check
    test_watch_command_is_registered already applies to the subcommand
    itself.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["serve", str(workdir / "does-not-exist.ipynb"), "--only", "add"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_serve_command_only_and_exclude_are_mutually_exclusive(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    proc = _run_cli(
        [
            "serve", str(notebook_path),
            "--only", "add", "--exclude", "add", "--port", "0",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "only and exclude can't both be given")


def test_watch_command_accepts_only_and_exclude_flags(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["watch", str(workdir / "does-not-exist.ipynb"), "--exclude", "helper"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_watch_command_only_and_exclude_are_mutually_exclusive(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    proc = _run_cli(
        ["watch", str(notebook_path), "--only", "add", "--exclude", "add"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "only and exclude can't both be given")


def test_diff_command_is_registered():
    """Same subparser/dispatch-branch mismatch gap test_deploy_command_is_registered
    (test_cli_deploy.py) and test_watch_command_is_registered (above)
    already guard against for `deploy`/`watch`.
    """

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "diff" in proc.stdout


def test_diff_command_reports_added_removed_and_changed_functions(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    old_path = workdir / "old.ipynb"
    new_path = workdir / "new.ipynb"

    _write_notebook_with_function(
        old_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n    return a - b\n",
    )
    _write_notebook_with_function(
        new_path,
        "def add(a: int, b: int, c: int = 0) -> int:\n    return a + b + c\n\n"
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
    )

    proc = _run_cli(["diff", str(old_path), str(new_path)], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Added 1 endpoint(s):" in proc.stdout
    assert "POST /multiply" in proc.stdout
    assert "Removed 1 endpoint(s):" in proc.stdout
    assert "POST /subtract" in proc.stdout
    assert "Changed 1 endpoint(s):" in proc.stdout
    assert "POST /add" in proc.stdout


def test_diff_command_reports_no_changes_for_identical_notebooks(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["diff", str(notebook_path), str(notebook_path)], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No changes to the compiled API surface." in proc.stdout


def test_diff_command_json_flag_emits_machine_readable_output(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    old_path = workdir / "old.ipynb"
    new_path = workdir / "new.ipynb"
    _write_notebook_with_function(
        old_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    _write_notebook_with_function(
        new_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
    )

    proc = _run_cli(
        ["diff", str(old_path), str(new_path), "--json"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    assert [f["name"] for f in data["added"]] == ["multiply"]
    assert data["removed"] == []
    assert data["changed"] == []
    assert data["unchanged"] == ["add"]


def test_diff_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["diff", str(notebook_path), str(workdir / "does-not-exist.ipynb")],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_diff_command_requires_both_notebook_arguments(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(["diff", str(notebook_path)], cwd=workdir)

    assert proc.returncode != 0
    assert "new_notebook" in proc.stderr


def test_export_curl_command_is_registered():
    """Same subparser/dispatch-branch mismatch gap test_deploy_command_is_registered
    (test_cli_deploy.py) and test_watch_command_is_registered (above)
    already guard against for `deploy`/`watch`.
    """

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "export-curl" in proc.stdout


def test_export_curl_command_writes_a_script_with_a_command_per_function(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n    return a - b\n",
    )

    proc = _run_cli(["export-curl", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cURL script written to: requests.sh (2 request(s))" in proc.stdout

    script = (workdir / "requests.sh").read_text(encoding="utf-8")
    assert "curl -X POST http://localhost:8000/add" in script
    assert "curl -X POST http://localhost:8000/subtract" in script
    assert "X-API-Key: notebook-to-api-dev-key" in script


def test_export_curl_command_respects_host_port_api_key_and_output(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    proc = _run_cli(
        [
            "export-curl", str(notebook_path),
            "--host", "api.example.com", "--port", "9000",
            "--api-key", "mykey123", "--output", "custom.sh",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    script = (workdir / "custom.sh").read_text(encoding="utf-8")
    assert "curl -X POST http://api.example.com:9000/add" in script
    assert "X-API-Key: mykey123" in script
    assert not (workdir / "requests.sh").exists()


def test_export_curl_command_json_flag_emits_machine_readable_output(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    proc = _run_cli(
        ["export-curl", str(notebook_path), "--json"], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    assert data["status"] == "success"
    assert data["path"] == "requests.sh"
    assert len(data["commands"]) == 1
    assert "curl -X POST http://localhost:8000/add" in data["commands"][0]


def test_export_curl_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["export-curl", str(workdir / "does-not-exist.ipynb")], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def _json_response(status_code, body):
    """Queue-entry helper for _FakeDashboardHandler.responses: a JSON
    body, encoded and content-typed the way every real dashboard JSON
    response already is.
    """
    return (status_code, json.dumps(body).encode("utf-8"), "application/json")


def _raw_response(status_code, content, content_type="application/x-ipynb+json"):
    """Queue-entry helper for _FakeDashboardHandler.responses: raw bytes,
    the same shape GET /api/notebooks/{filename}'s own FileResponse
    actually returns (a notebook's content, not a JSON envelope).
    """
    return (status_code, content, content_type)


class _FakeDashboardHandler(http.server.BaseHTTPRequestHandler):
    """A minimal stand-in for a running dashboard, used only to exercise
    the `upload`/`list`/`download` CLI commands' own HTTP handling
    (request construction, success/error response handling,
    connection-failure handling) -- not to re-verify the dashboard's own
    route behavior (multipart parsing, validation, atomic writes, the
    actual notebook listing/lookup, ...), which is already exhaustively
    covered directly in tests/test_upload_routes.py.

    `responses` is consumed FIFO, one (status_code, payload_bytes,
    content_type) entry per request received (see _json_response/
    _raw_response above); `requests` records each request's raw path
    (including its query string) so a test can confirm e.g. "search" or
    "overwrite" was actually passed through.

    `response_headers` is an optional, separately-consumed FIFO queue of
    extra {header_name: value} dicts, one per request, for tests that
    need to control a response header `responses`' own (status_code,
    payload, content_type) shape has no room for -- e.g. Content-
    Disposition, which GET /api/download's own remote-build CLI command
    reads to pick a default --output filename. A response with nothing
    queued here just gets no extra headers, the same as before this
    existed.
    """

    responses = []
    requests = []
    bodies = []
    response_headers = []

    def _handle(self):

        if self.command in ("POST", "PATCH", "PUT"):
            content_length = int(self.headers.get("Content-Length", 0))
            type(self).bodies.append(self.rfile.read(content_length))
        else:
            type(self).bodies.append(b"")

        type(self).requests.append(self.path)

        status_code, payload, content_type = type(self).responses.pop(0)

        extra_headers = (
            type(self).response_headers.pop(0)
            if type(self).response_headers else {}
        )

        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for header_name, value in extra_headers.items():
            self.send_header(header_name, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        self._handle()

    def do_GET(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_dashboard():
    _FakeDashboardHandler.responses = []
    _FakeDashboardHandler.requests = []
    _FakeDashboardHandler.bodies = []
    _FakeDashboardHandler.response_headers = []

    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeDashboardHandler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        yield f"http://127.0.0.1:{port}", _FakeDashboardHandler
    finally:
        server.shutdown()
        server_thread.join(timeout=5)


def test_upload_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "upload" in proc.stdout


def test_upload_command_reports_success(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "filename": "nb.ipynb",
            "path": "/srv/uploads/nb.ipynb",
            "overwritten": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["upload", str(notebook_path), "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Uploaded 'nb.ipynb'" in proc.stdout
    assert "overwritten: False" in proc.stdout
    assert handler.requests == ["/api/upload?overwrite=false"]


def test_upload_command_passes_the_overwrite_flag_through(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "path": "/srv/uploads/nb.ipynb", "overwritten": True,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        [
            "upload", str(notebook_path),
            "--dashboard-url", dashboard_url, "--overwrite",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert handler.requests == ["/api/upload?overwrite=true"]


def test_upload_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "path": "/srv/uploads/nb.ipynb", "overwritten": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["upload", str(notebook_path), "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == {
        "status": "success", "filename": "nb.ipynb",
        "path": "/srv/uploads/nb.ipynb", "overwritten": False,
    }


def test_upload_command_reports_a_clean_error_for_a_rejected_upload(
    tmp_path, fake_dashboard
):
    """A 409 (same-name collision without --overwrite), or any other
    non-2xx the dashboard returns, must surface as the same clean
    "Error: ..." single-line message every other core command's expected
    failure modes already get, using the dashboard's own {"detail": ...}
    body -- not a raw HTTP response dump.
    """

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(409, {"detail": "A notebook named 'nb.ipynb' already exists."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        ["upload", str(notebook_path), "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "already exists")


def test_upload_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    proc = _run_cli(
        [
            "upload", str(notebook_path),
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_upload_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):
    """The local file must be checked before ever attempting to reach the
    dashboard -- no server is running for this test at all, so a
    connection-error message here (instead of the missing-file one) would
    mean the CLI tried to open a request before validating its own input.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "upload", str(workdir / "does-not-exist.ipynb"),
            "--dashboard-url", "http://127.0.0.1:1",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_upload_command_with_multiple_notebooks_hits_the_batch_endpoint(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "results": [
                {
                    "status": "success", "filename": "a.ipynb",
                    "path": "/srv/uploads/a.ipynb", "overwritten": False,
                },
                {
                    "status": "success", "filename": "b.ipynb",
                    "path": "/srv/uploads/b.ipynb", "overwritten": False,
                },
            ],
            "succeeded_count": 2,
            "failed_count": 0,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_a = workdir / "a.ipynb"
    notebook_b = workdir / "b.ipynb"
    _write_notebook(notebook_a)
    _write_notebook(notebook_b)

    proc = _run_cli(
        [
            "upload", str(notebook_a), str(notebook_b),
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Uploaded 'a.ipynb'" in proc.stdout
    assert "Uploaded 'b.ipynb'" in proc.stdout
    assert "2 succeeded, 0 failed." in proc.stdout
    assert handler.requests == ["/api/upload/batch?overwrite=false"]


def test_upload_command_with_multiple_notebooks_reports_per_file_failures(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "results": [
                {
                    "status": "success", "filename": "a.ipynb",
                    "path": "/srv/uploads/a.ipynb", "overwritten": False,
                },
                {
                    "status": "error", "filename": "b.ipynb",
                    "detail": "A notebook named 'b.ipynb' already exists.",
                },
            ],
            "succeeded_count": 1,
            "failed_count": 1,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_a = workdir / "a.ipynb"
    notebook_b = workdir / "b.ipynb"
    _write_notebook(notebook_a)
    _write_notebook(notebook_b)

    proc = _run_cli(
        [
            "upload", str(notebook_a), str(notebook_b),
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Uploaded 'a.ipynb'" in proc.stdout
    assert "Failed 'b.ipynb': A notebook named 'b.ipynb' already exists." in proc.stdout
    assert "1 succeeded, 1 failed." in proc.stdout


def test_upload_command_with_multiple_notebooks_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "results": [
                {
                    "status": "success", "filename": "a.ipynb",
                    "path": "/srv/uploads/a.ipynb", "overwritten": False,
                },
            ],
            "succeeded_count": 1,
            "failed_count": 0,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_a = workdir / "a.ipynb"
    _write_notebook(notebook_a)

    proc = _run_cli(
        [
            "upload", str(notebook_a),
            "--dashboard-url", dashboard_url, "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["succeeded_count"] == 1


def test_upload_command_with_multiple_notebooks_passes_the_overwrite_flag_through(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "results": [], "succeeded_count": 0, "failed_count": 0,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_a = workdir / "a.ipynb"
    notebook_b = workdir / "b.ipynb"
    _write_notebook(notebook_a)
    _write_notebook(notebook_b)

    proc = _run_cli(
        [
            "upload", str(notebook_a), str(notebook_b),
            "--dashboard-url", dashboard_url, "--overwrite",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert handler.requests == ["/api/upload/batch?overwrite=true"]


def test_upload_command_with_multiple_notebooks_reports_a_clean_error_for_a_missing_notebook(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_a = workdir / "a.ipynb"
    _write_notebook(notebook_a)

    proc = _run_cli(
        [
            "upload", str(notebook_a), str(workdir / "does-not-exist.ipynb"),
            "--dashboard-url", "http://127.0.0.1:1",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_list_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "list" in proc.stdout


def test_list_command_prints_notebooks_from_the_dashboard(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebooks": [
                {
                    "filename": "add.ipynb", "size_bytes": 123,
                    "modified_at": "2026-01-01T00:00:00+00:00",
                    "currently_compiled": True, "tags": ["prod"],
                },
                {
                    "filename": "scratch.ipynb", "size_bytes": 45,
                    "modified_at": "2026-01-02T00:00:00+00:00",
                    "currently_compiled": False, "tags": [],
                },
            ],
            "total_count": 2, "limit": None, "offset": 0,
        })
    ]

    proc = _run_cli(["list", "--dashboard-url", dashboard_url], cwd=Path.cwd())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "add.ipynb  (123 bytes)  [currently compiled; tags: prod]" in proc.stdout
    assert "scratch.ipynb  (45 bytes)" in proc.stdout
    assert "2 notebook(s) total." in proc.stdout
    assert handler.requests == ["/api/notebooks?sort=name&order=asc&offset=0"]


def test_list_command_passes_the_search_flag_through(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "notebooks": [], "total_count": 0,
            "limit": None, "offset": 0,
        })
    ]

    proc = _run_cli(
        ["list", "--dashboard-url", dashboard_url, "--search", "add"],
        cwd=Path.cwd(),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert handler.requests == ["/api/notebooks?sort=name&order=asc&offset=0&search=add"]


def test_list_command_passes_sort_order_tag_and_limit_through(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "notebooks": [], "total_count": 0,
            "limit": 5, "offset": 0,
        })
    ]

    proc = _run_cli(
        [
            "list", "--dashboard-url", dashboard_url,
            "--sort", "modified", "--order", "desc", "--tag", "prod", "--limit", "5",
        ],
        cwd=Path.cwd(),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert handler.requests == [
        "/api/notebooks?sort=modified&order=desc&offset=0&tag=prod&limit=5"
    ]


def test_list_command_passes_offset_through(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "notebooks": [], "total_count": 0,
            "limit": None, "offset": 10,
        })
    ]

    proc = _run_cli(
        ["list", "--dashboard-url", dashboard_url, "--offset", "10"],
        cwd=Path.cwd(),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert handler.requests == ["/api/notebooks?sort=name&order=asc&offset=10"]


def test_list_command_reports_a_partial_page_when_limited(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebooks": [
                {
                    "filename": "a.ipynb", "size_bytes": 1,
                    "modified_at": "2026-01-01T00:00:00+00:00",
                    "currently_compiled": False, "tags": [],
                },
            ],
            "total_count": 5, "limit": 1, "offset": 0,
        })
    ]

    proc = _run_cli(
        ["list", "--dashboard-url", dashboard_url, "--limit", "1"],
        cwd=Path.cwd(),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Showing 1 of 5 notebook(s) (offset 0)." in proc.stdout


def test_list_command_rejects_an_invalid_sort_value(fake_dashboard):

    dashboard_url, handler = fake_dashboard

    proc = _run_cli(
        ["list", "--dashboard-url", dashboard_url, "--sort", "bogus"],
        cwd=Path.cwd(),
    )

    assert proc.returncode != 0
    assert "invalid choice" in proc.stderr


def test_list_command_reports_no_notebooks_found(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "notebooks": [], "total_count": 0,
            "limit": None, "offset": 0,
        })
    ]

    proc = _run_cli(["list", "--dashboard-url", dashboard_url], cwd=Path.cwd())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No notebooks found." in proc.stdout


def test_list_command_json_flag_emits_the_dashboards_own_response(fake_dashboard):

    dashboard_url, handler = fake_dashboard
    body = {
        "status": "success",
        "notebooks": [{
            "filename": "add.ipynb", "size_bytes": 123,
            "modified_at": "2026-01-01T00:00:00+00:00",
            "currently_compiled": False, "tags": [],
        }],
        "total_count": 1, "limit": None, "offset": 0,
    }
    handler.responses = [_json_response(200, body)]

    proc = _run_cli(
        ["list", "--dashboard-url", dashboard_url, "--json"], cwd=Path.cwd()
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == body


def test_list_command_reports_a_clean_error_when_the_dashboard_is_unreachable():

    proc = _run_cli(
        ["list", "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5"],
        cwd=Path.cwd(),
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_download_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "download" in proc.stdout


def test_download_command_saves_the_notebook_to_the_default_path(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    notebook_bytes = b'{"cells": [], "nbformat": 4, "nbformat_minor": 5, "metadata": {}}'
    handler.responses = [_raw_response(200, notebook_bytes)]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["download", "nb.ipynb", "--dashboard-url", dashboard_url], cwd=workdir
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"({len(notebook_bytes)} bytes)" in proc.stdout
    assert (workdir / "nb.ipynb").read_bytes() == notebook_bytes
    assert handler.requests == ["/api/notebooks/nb.ipynb"]


def test_download_command_respects_a_custom_output_path(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    notebook_bytes = b'{"cells": [], "nbformat": 4, "nbformat_minor": 5, "metadata": {}}'
    handler.responses = [_raw_response(200, notebook_bytes)]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "download", "nb.ipynb", "--dashboard-url", dashboard_url,
            "--output", "saved_here.ipynb",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "saved_here.ipynb").read_bytes() == notebook_bytes
    assert not (workdir / "nb.ipynb").exists()


def test_download_command_json_flag_emits_a_machine_readable_result(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    notebook_bytes = b'{"cells": []}'
    handler.responses = [_raw_response(200, notebook_bytes)]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["download", "nb.ipynb", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == {
        "status": "success",
        "filename": "nb.ipynb",
        "path": "nb.ipynb",
        "size_bytes": len(notebook_bytes),
    }


def test_download_command_reports_a_clean_error_for_a_missing_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook file not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["download", "does-not-exist.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook file not found")
    assert not (workdir / "does-not-exist.ipynb").exists()


def test_download_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "download", "nb.ipynb",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_delete_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "delete" in proc.stdout


def test_delete_command_reports_success_with_yes_flag(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "filename": "nb.ipynb",
            "was_currently_compiled": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "nb.ipynb", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Deleted 'nb.ipynb'" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb"]


def test_delete_command_flags_when_the_currently_compiled_notebook_was_deleted(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "filename": "nb.ipynb",
            "was_currently_compiled": True,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "nb.ipynb", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "currently compiled app" in proc.stdout


def test_delete_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "filename": "nb.ipynb",
            "was_currently_compiled": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "nb.ipynb", "--dashboard-url", dashboard_url, "--yes", "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["filename"] == "nb.ipynb"


def test_delete_command_aborts_without_yes_when_declined(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = []

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = subprocess.run(
        [
            sys.executable, "-m", "backend.cli",
            "delete", "nb.ipynb", "--dashboard-url", dashboard_url,
        ],
        cwd=str(workdir),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        input="n\n",
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Aborted." in proc.stdout
    assert handler.requests == []


def test_delete_command_reports_a_clean_error_for_a_missing_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook file not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "nb.ipynb", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook file not found")


def test_delete_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "delete", "nb.ipynb",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5", "--yes",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_delete_command_all_flag_reports_success_with_yes_flag(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "deleted_count": 2,
            "deleted_filenames": ["a.ipynb", "b.ipynb"],
            "currently_compiled_notebook_deleted": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "--all", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Deleted 'a.ipynb'" in proc.stdout
    assert "Deleted 'b.ipynb'" in proc.stdout
    assert "2 notebook(s) deleted" in proc.stdout
    assert handler.requests == ["/api/notebooks?confirm=true"]


def test_delete_command_all_flag_reports_nothing_to_delete(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "deleted_count": 0,
            "deleted_filenames": [], "currently_compiled_notebook_deleted": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "--all", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No notebooks to delete" in proc.stdout


def test_delete_command_all_flag_flags_the_currently_compiled_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "deleted_count": 1,
            "deleted_filenames": ["a.ipynb"],
            "currently_compiled_notebook_deleted": True,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "--all", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "currently compiled app" in proc.stdout


def test_delete_command_all_flag_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "deleted_count": 1,
            "deleted_filenames": ["a.ipynb"],
            "currently_compiled_notebook_deleted": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "--all", "--dashboard-url", dashboard_url, "--yes", "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["deleted_count"] == 1


def test_delete_command_all_flag_aborts_without_yes_when_declined(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = []

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = subprocess.run(
        [
            sys.executable, "-m", "backend.cli",
            "delete", "--all", "--dashboard-url", dashboard_url,
        ],
        cwd=str(workdir),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        input="n\n",
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Aborted." in proc.stdout
    assert handler.requests == []


def test_delete_command_rejects_both_filename_and_all(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "nb.ipynb", "--all", "--dashboard-url", "http://127.0.0.1:1", "--yes"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Pass either a filename or --all, not both.")


def test_delete_command_rejects_neither_filename_nor_all(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["delete", "--dashboard-url", "http://127.0.0.1:1", "--yes"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Pass a filename to delete, or --all")


def test_delete_command_all_flag_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "delete", "--all",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5", "--yes",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_rename_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "rename" in proc.stdout


def test_rename_command_reports_success(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "filename": "nb.ipynb",
            "new_filename": "renamed.ipynb",
            "was_currently_compiled": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["rename", "nb.ipynb", "renamed.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Renamed 'nb.ipynb' to 'renamed.ipynb'" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb"]
    assert json.loads(handler.bodies[0]) == {
        "new_filename": "renamed.ipynb", "overwrite": False,
    }


def test_rename_command_passes_the_overwrite_flag_through(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "new_filename": "renamed.ipynb", "was_currently_compiled": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "rename", "nb.ipynb", "renamed.ipynb",
            "--dashboard-url", dashboard_url, "--overwrite",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(handler.bodies[0])["overwrite"] is True


def test_rename_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "new_filename": "renamed.ipynb", "was_currently_compiled": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "rename", "nb.ipynb", "renamed.ipynb",
            "--dashboard-url", dashboard_url, "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["new_filename"] == "renamed.ipynb"


def test_rename_command_reports_a_clean_error_for_a_rejected_rename(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(409, {
            "detail": "A notebook named 'renamed.ipynb' already exists."
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["rename", "nb.ipynb", "renamed.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "already exists")


def test_rename_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "rename", "nb.ipynb", "renamed.ipynb",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_tags_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "tags" in proc.stdout


def test_tags_get_command_prints_the_notebooks_tags(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb", "tags": ["prod", "v2"],
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["tags", "get", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nb.ipynb: prod, v2" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb/tags"]


def test_tags_get_command_reports_no_tags(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "filename": "nb.ipynb", "tags": []})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["tags", "get", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nb.ipynb: (no tags)" in proc.stdout


def test_tags_get_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb", "tags": ["prod"],
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["tags", "get", "nb.ipynb", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["tags"] == ["prod"]


def test_tags_set_command_replaces_the_notebooks_tags(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb", "tags": ["prod", "v2"],
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["tags", "set", "nb.ipynb", "prod", "v2", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nb.ipynb tags set to: prod, v2" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb/tags"]
    assert json.loads(handler.bodies[0]) == {"tags": ["prod", "v2"]}


def test_tags_set_command_with_no_tags_clears_them(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "filename": "nb.ipynb", "tags": []})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["tags", "set", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nb.ipynb tags set to: (none)" in proc.stdout
    assert json.loads(handler.bodies[0]) == {"tags": []}


def test_tags_set_command_reports_a_clean_error_for_an_invalid_tag(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(400, {"detail": "Each tag must be a non-empty string"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["tags", "set", "nb.ipynb", "", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "non-empty string")


def test_tags_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "tags", "get", "nb.ipynb",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_validate_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "validate" in proc.stdout


def test_validate_command_passes_a_clean_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    proc = _run_cli(["validate", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No issues found." in proc.stdout


def test_validate_command_warns_but_does_not_fail_on_skipped_functions(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path,
        "def unsupported(a, **kwargs):\n    return a\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    proc = _run_cli(["validate", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Skipped functions" in proc.stdout
    assert "unsupported" in proc.stdout
    assert "still compile cleanly" in proc.stdout


def test_validate_command_strict_flag_fails_on_skipped_functions(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def unsupported(a, **kwargs):\n    return a\n"
    )

    proc = _run_cli(["validate", str(notebook_path), "--strict"], cwd=workdir)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "Validation failed." in proc.stdout


def test_validate_command_fails_on_a_reserved_name_conflict_even_without_strict(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def health_check() -> dict:\n    return {}\n"
    )

    proc = _run_cli(["validate", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "Reserved name conflicts" in proc.stdout
    assert "health_check" in proc.stdout
    assert "Validation failed." in proc.stdout


def test_validate_command_json_flag_emits_a_machine_readable_status(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def health_check() -> dict:\n    return {}\n"
    )

    proc = _run_cli(["validate", str(notebook_path), "--json"], cwd=workdir)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "fail"
    assert data["reserved_name_conflicts"] == ["health_check"]
    assert data["skipped_functions"] == []


def test_validate_command_json_flag_reports_pass_status_for_a_clean_notebook(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    proc = _run_cli(["validate", str(notebook_path), "--json"], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "pass"


def test_validate_command_does_not_create_any_output_directory(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook_with_function(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    proc = _run_cli(["validate", str(notebook_path)], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (workdir / "generated").exists()


def test_validate_command_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["validate", str(workdir / "does-not-exist.ipynb")], cwd=workdir
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_remote_compile_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "remote-compile" in proc.stdout


def test_remote_compile_command_reports_success(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebook": "nb.ipynb",
            "functions": [{"name": "add"}],
            "endpoints": [{"path": "/add", "method": "POST", "is_async": False}],
            "skipped_functions": [],
            "dependencies": ["fastapi"],
            "generated_files": ["app.py"],
            "message": "Notebook compiled successfully",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-compile", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Compiled 'nb.ipynb'" in proc.stdout
    assert "POST /add" in proc.stdout
    assert "Dependencies: fastapi" in proc.stdout
    assert handler.requests == ["/api/compile"]
    assert json.loads(handler.bodies[0]) == {"notebook_path": "nb.ipynb"}


def test_remote_compile_command_passes_only_through_to_the_dashboard(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebook": "nb.ipynb",
            "functions": [{"name": "add"}],
            "endpoints": [{"path": "/add", "method": "POST", "is_async": False}],
            "skipped_functions": [],
            "dependencies": [],
            "generated_files": [],
            "message": "Notebook compiled successfully",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-compile", "nb.ipynb",
            "--dashboard-url", dashboard_url, "--only", "add, subtract",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(handler.bodies[0]) == {
        "notebook_path": "nb.ipynb", "only": ["add", "subtract"],
    }


def test_remote_compile_command_passes_exclude_through_to_the_dashboard(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebook": "nb.ipynb",
            "functions": [{"name": "add"}],
            "endpoints": [{"path": "/add", "method": "POST", "is_async": False}],
            "skipped_functions": [],
            "dependencies": [],
            "generated_files": [],
            "message": "Notebook compiled successfully",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-compile", "nb.ipynb",
            "--dashboard-url", dashboard_url, "--exclude", "subtract",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(handler.bodies[0]) == {
        "notebook_path": "nb.ipynb", "exclude": ["subtract"],
    }


def test_remote_compile_command_reports_the_dashboards_error_for_conflicting_only_and_exclude(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(
            400,
            {"detail": "only and exclude can't both be given -- choose one."},
        )
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-compile", "nb.ipynb",
            "--dashboard-url", dashboard_url,
            "--only", "add", "--exclude", "subtract",
        ],
        cwd=workdir,
    )

    assert proc.returncode != 0
    assert "only and exclude" in proc.stdout + proc.stderr


def test_remote_compile_command_flags_background_endpoints(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebook": "nb.ipynb",
            "functions": [],
            "endpoints": [{"path": "/train_model", "method": "POST", "is_async": True}],
            "skipped_functions": [],
            "dependencies": [],
            "generated_files": [],
            "message": "Notebook compiled successfully",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-compile", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "POST /train_model  [background]" in proc.stdout


def test_remote_compile_command_reports_skipped_functions(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "notebook": "nb.ipynb",
            "functions": [],
            "endpoints": [],
            "skipped_functions": [{"name": "unsupported", "reason": "uses **kwargs"}],
            "dependencies": [],
            "generated_files": [],
            "message": "Notebook compiled successfully",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-compile", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped function(s):" in proc.stdout
    assert "unsupported: uses **kwargs" in proc.stdout


def test_remote_compile_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "notebook": "nb.ipynb", "functions": [],
            "endpoints": [], "skipped_functions": [], "dependencies": [],
            "generated_files": [], "message": "Notebook compiled successfully",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-compile", "nb.ipynb", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["notebook"] == "nb.ipynb"


def test_remote_compile_command_reports_a_clean_error_for_a_missing_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook file not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-compile", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook file not found")


def test_remote_compile_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-compile", "nb.ipynb",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_remote_build_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "remote-build" in proc.stdout


def test_remote_build_command_saves_the_zip_using_the_content_disposition_name(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    zip_bytes = b"PK\x03\x04fake-zip-content"
    handler.responses = [
        (
            200,
            zip_bytes,
            "application/zip",
        )
    ]
    handler.response_headers = [{"Content-Disposition": 'attachment; filename="generated.zip"'}]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-build", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "generated.zip" in proc.stdout
    assert (workdir / "generated.zip").read_bytes() == zip_bytes
    assert handler.requests == ["/api/download"]


def test_remote_build_command_respects_a_custom_output_path(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    zip_bytes = b"PK\x03\x04fake-zip-content"
    handler.responses = [(200, zip_bytes, "application/zip")]
    handler.response_headers = [{"Content-Disposition": 'attachment; filename="generated.zip"'}]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-build", "--dashboard-url", dashboard_url,
            "--output", "my-build.zip",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "my-build.zip").read_bytes() == zip_bytes
    assert not (workdir / "generated.zip").exists()


def test_remote_build_command_json_flag_emits_a_machine_readable_result(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    zip_bytes = b"PK\x03\x04fake-zip-content"
    handler.responses = [(200, zip_bytes, "application/zip")]
    handler.response_headers = [{"Content-Disposition": 'attachment; filename="generated.zip"'}]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-build", "--dashboard-url", dashboard_url, "--json"], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "success"
    assert data["size_bytes"] == len(zip_bytes)


def test_remote_build_command_reports_a_clean_error_when_no_app_is_compiled(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "No compiled app found. Run /api/compile first."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-build", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No compiled app found")


def test_remote_build_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-build",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_versions_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "versions" in proc.stdout


def test_versions_list_command_prints_the_notebooks_versions(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "filename": "nb.ipynb",
            "versions": [
                {
                    "version_id": "20260101T000000Z-abcdef.ipynb",
                    "size_bytes": 512,
                    "saved_at": "2026-01-01T00:00:00+00:00",
                },
            ],
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["versions", "list", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "20260101T000000Z-abcdef.ipynb" in proc.stdout
    assert "512 bytes" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb/versions"]


def test_versions_list_command_reports_no_saved_versions(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "filename": "nb.ipynb", "versions": []})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["versions", "list", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No saved versions for 'nb.ipynb'." in proc.stdout


def test_versions_list_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "versions": [
                {"version_id": "v1.ipynb", "size_bytes": 10, "saved_at": "2026-01-01T00:00:00+00:00"}
            ],
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["versions", "list", "nb.ipynb", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["versions"][0]["version_id"] == "v1.ipynb"


def test_versions_list_command_reports_a_clean_error_for_a_missing_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook file not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["versions", "list", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook file not found")


def test_versions_get_command_downloads_a_version_to_the_default_path(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _raw_response(200, b'{"nbformat": 4, "cells": []}')
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "get", "nb.ipynb", "v1.ipynb",
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "v1.ipynb").read_bytes() == b'{"nbformat": 4, "cells": []}'
    assert handler.requests == ["/api/notebooks/nb.ipynb/versions/v1.ipynb"]


def test_versions_get_command_respects_a_custom_output_path(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [_raw_response(200, b"notebook-bytes")]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "get", "nb.ipynb", "v1.ipynb",
            "--dashboard-url", dashboard_url, "--output", "restored.ipynb",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "restored.ipynb").read_bytes() == b"notebook-bytes"
    assert not (workdir / "v1.ipynb").exists()


def test_versions_get_command_json_flag_emits_a_machine_readable_result(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [_raw_response(200, b"notebook-bytes")]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "get", "nb.ipynb", "v1.ipynb",
            "--dashboard-url", dashboard_url, "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["version_id"] == "v1.ipynb"
    assert data["size_bytes"] == len(b"notebook-bytes")


def test_versions_get_command_reports_a_clean_error_for_a_missing_version(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook version not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "get", "nb.ipynb", "does-not-exist.ipynb",
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook version not found")


def test_versions_restore_command_reports_success(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "restored_version_id": "v1.ipynb",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "restore", "nb.ipynb", "v1.ipynb",
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Restored 'nb.ipynb' to version 'v1.ipynb'" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb/versions/v1.ipynb/restore"]


def test_versions_restore_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "nb.ipynb",
            "restored_version_id": "v1.ipynb",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "restore", "nb.ipynb", "v1.ipynb",
            "--dashboard-url", dashboard_url, "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["restored_version_id"] == "v1.ipynb"


def test_versions_restore_command_reports_a_clean_error_for_a_missing_version(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook version not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "restore", "nb.ipynb", "does-not-exist.ipynb",
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook version not found")


def test_versions_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "versions", "list", "nb.ipynb",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_remote_files_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "remote-files" in proc.stdout


def test_remote_files_list_command_prints_the_compiled_files(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "generated_files": ["app.py", "requirements.txt"],
            "file_details": [
                {"filename": "app.py", "size_bytes": 1024, "modified_at": "2026-01-01T00:00:00+00:00"},
                {"filename": "requirements.txt", "size_bytes": 12, "modified_at": "2026-01-01T00:00:00+00:00"},
            ],
            "compiled_at": "2026-01-01T00:00:00+00:00",
            "source_notebook_filename": "nb.ipynb",
            "source_notebook_exists": True,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "list", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "app.py  (1024 bytes, modified 2026-01-01T00:00:00+00:00)" in proc.stdout
    assert "Compiled from: nb.ipynb" in proc.stdout
    assert handler.requests == ["/api/generated"]


def test_remote_files_list_command_flags_a_missing_source_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success",
            "generated_files": ["app.py"],
            "file_details": [
                {"filename": "app.py", "size_bytes": 1024, "modified_at": "2026-01-01T00:00:00+00:00"},
            ],
            "compiled_at": "2026-01-01T00:00:00+00:00",
            "source_notebook_filename": "nb.ipynb",
            "source_notebook_exists": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "list", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Compiled from: nb.ipynb  [no longer uploaded]" in proc.stdout


def test_remote_files_list_command_reports_no_compiled_app(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "generated_files": [], "file_details": [],
            "compiled_at": None, "source_notebook_filename": None,
            "source_notebook_exists": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "list", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No compiled app found on the dashboard." in proc.stdout


def test_remote_files_list_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "generated_files": ["app.py"],
            "file_details": [
                {"filename": "app.py", "size_bytes": 1, "modified_at": "2026-01-01T00:00:00+00:00"}
            ],
            "compiled_at": "2026-01-01T00:00:00+00:00",
            "source_notebook_filename": None, "source_notebook_exists": False,
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "list", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["generated_files"] == ["app.py"]


def test_remote_files_get_command_prints_content_to_stdout_by_default(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "app.py",
            "content": "from fastapi import FastAPI\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "get", "app.py", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout == "from fastapi import FastAPI\n"
    assert handler.requests == ["/api/generated/app.py"]


def test_remote_files_get_command_saves_to_output_when_given(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "requirements.txt",
            "content": "fastapi\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-files", "get", "requirements.txt",
            "--dashboard-url", dashboard_url, "--output", "reqs.txt",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "reqs.txt").read_text() == "fastapi\n"
    assert "Saved 'requirements.txt'" in proc.stdout


def test_remote_files_get_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "filename": "app.py", "content": "x = 1\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-files", "get", "app.py",
            "--dashboard-url", dashboard_url, "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["content"] == "x = 1\n"


def test_remote_files_get_command_reports_a_clean_error_for_a_missing_file(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Generated file not found. Run /api/compile first."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "get", "missing.py", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Generated file not found")


def test_remote_files_delete_command_reports_success_with_yes_flag(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "generated_dir": "/srv/generated"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "delete", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Deleted the compiled app" in proc.stdout
    assert handler.requests == ["/api/generated"]


def test_remote_files_delete_command_aborts_without_yes_when_declined(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = []

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = subprocess.run(
        [
            sys.executable, "-m", "backend.cli",
            "remote-files", "delete", "--dashboard-url", dashboard_url,
        ],
        cwd=str(workdir),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        input="n\n",
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Aborted." in proc.stdout
    assert handler.requests == []


def test_remote_files_delete_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "generated_dir": "/srv/generated"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-files", "delete",
            "--dashboard-url", dashboard_url, "--yes", "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["generated_dir"] == "/srv/generated"


def test_remote_files_delete_command_reports_a_clean_error_when_nothing_is_compiled(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "No compiled app found. Run /api/compile first."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-files", "delete", "--dashboard-url", dashboard_url, "--yes"],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No compiled app found")


def test_remote_files_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-files", "list",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def _notebook_bytes_with_function(function_source):
    """The exact bytes _write_notebook_with_function writes to disk,
    without writing to disk -- for queuing as a fake dashboard's own GET
    /api/notebooks/{filename} response body in the remote-diff tests
    below, which need "the dashboard's copy" to exist only as response
    bytes, never as a local file.
    """
    return json.dumps(
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
                    "source": function_source,
                }
            ],
        }
    ).encode("utf-8")


def test_remote_diff_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "remote-diff" in proc.stdout


def test_remote_diff_command_reports_added_removed_and_changed_functions(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _raw_response(
            200,
            _notebook_bytes_with_function(
                "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                "def subtract(a: int, b: int) -> int:\n    return a - b\n"
            ),
        )
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    local_path = workdir / "local.ipynb"
    _write_notebook_with_function(
        local_path,
        "def add(a: int, b: int, c: int = 0) -> int:\n    return a + b + c\n\n"
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
    )

    proc = _run_cli(
        [
            "remote-diff", "nb.ipynb", str(local_path),
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Comparing local" in proc.stdout
    assert "Added 1 endpoint(s):" in proc.stdout
    assert "POST /multiply" in proc.stdout
    assert "Removed 1 endpoint(s):" in proc.stdout
    assert "POST /subtract" in proc.stdout
    assert "Changed 1 endpoint(s):" in proc.stdout
    assert "POST /add" in proc.stdout
    assert handler.requests == ["/api/notebooks/nb.ipynb"]


def test_remote_diff_command_reports_no_changes_for_identical_notebooks(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    function_source = "def add(a: int, b: int) -> int:\n    return a + b\n"
    handler.responses = [
        _raw_response(200, _notebook_bytes_with_function(function_source))
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    local_path = workdir / "local.ipynb"
    _write_notebook_with_function(local_path, function_source)

    proc = _run_cli(
        [
            "remote-diff", "nb.ipynb", str(local_path),
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No changes to the compiled API surface." in proc.stdout


def test_remote_diff_command_defaults_the_local_path_to_the_filename(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    function_source = "def add(a: int, b: int) -> int:\n    return a + b\n"
    handler.responses = [
        _raw_response(200, _notebook_bytes_with_function(function_source))
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    # No explicit local path passed -- must default to "nb.ipynb" in cwd,
    # matching the dashboard-side filename.
    _write_notebook_with_function(workdir / "nb.ipynb", function_source)

    proc = _run_cli(
        ["remote-diff", "nb.ipynb", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No changes to the compiled API surface." in proc.stdout


def test_remote_diff_command_json_flag_emits_machine_readable_output(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _raw_response(
            200,
            _notebook_bytes_with_function(
                "def add(a: int, b: int) -> int:\n    return a + b\n"
            ),
        )
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    local_path = workdir / "local.ipynb"
    _write_notebook_with_function(
        local_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
    )

    proc = _run_cli(
        [
            "remote-diff", "nb.ipynb", str(local_path),
            "--dashboard-url", dashboard_url, "--json",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert [f["name"] for f in data["added"]] == ["multiply"]
    assert data["removed"] == []


def test_remote_diff_command_reports_a_clean_error_for_a_missing_local_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _raw_response(
            200,
            _notebook_bytes_with_function(
                "def add(a: int, b: int) -> int:\n    return a + b\n"
            ),
        )
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-diff", "nb.ipynb", str(workdir / "does-not-exist.ipynb"),
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No such file or directory")


def test_remote_diff_command_reports_a_clean_error_for_a_missing_remote_notebook(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "Notebook file not found"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    local_path = workdir / "local.ipynb"
    _write_notebook(local_path)

    proc = _run_cli(
        [
            "remote-diff", "nb.ipynb", str(local_path),
            "--dashboard-url", dashboard_url,
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Notebook file not found")


def test_remote_diff_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    local_path = workdir / "local.ipynb"
    _write_notebook(local_path)

    proc = _run_cli(
        [
            "remote-diff", "nb.ipynb", str(local_path),
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_remote_export_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "remote-export" in proc.stdout


def test_remote_export_openapi_command_prints_the_schema_to_stdout_by_default(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "format": "json",
            "path": "/srv/generated/openapi.json",
            "schema": {"openapi": "3.1.0", "info": {"title": "x"}},
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-export", "openapi", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    printed = json.loads(proc.stdout)
    assert printed == {"openapi": "3.1.0", "info": {"title": "x"}}
    assert handler.requests == ["/api/export-openapi"]
    assert json.loads(handler.bodies[0]) == {"format": "json"}


def test_remote_export_openapi_command_saves_to_output_when_given(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "format": "yaml",
            "path": "/srv/generated/openapi.yaml",
            "content": "openapi: 3.1.0\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-export", "openapi", "--format", "yaml",
            "--dashboard-url", dashboard_url, "--output", "schema.yaml",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "schema.yaml").read_text() == "openapi: 3.1.0\n"
    assert "Saved the OpenAPI yaml export" in proc.stdout
    assert json.loads(handler.bodies[0]) == {"format": "yaml"}


def test_remote_export_openapi_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "format": "json",
            "path": "/srv/generated/openapi.json",
            "schema": {"openapi": "3.1.0"},
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-export", "openapi", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["format"] == "json"
    assert data["schema"] == {"openapi": "3.1.0"}


def test_remote_export_openapi_command_reports_a_clean_error_when_nothing_is_compiled(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "No compiled app found. Run /api/compile first."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-export", "openapi", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No compiled app found")


def test_remote_export_sdk_command_prints_the_code_to_stdout_by_default(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "language": "python",
            "path": "/srv/generated/sdk/python_client.py",
            "code": "class Client:\n    pass\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-export", "sdk", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout == "class Client:\n    pass\n\n"
    assert handler.requests == ["/api/export-sdk"]
    assert json.loads(handler.bodies[0]) == {"language": "python"}


def test_remote_export_sdk_command_saves_to_output_when_given(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "language": "typescript",
            "path": "/srv/generated/sdk/typescript_client.ts",
            "code": "export class Client {}\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-export", "sdk", "--language", "typescript",
            "--dashboard-url", dashboard_url, "--output", "client.ts",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "client.ts").read_text() == "export class Client {}\n"
    assert "Saved the typescript SDK client" in proc.stdout
    assert json.loads(handler.bodies[0]) == {"language": "typescript"}


def test_remote_export_sdk_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "success", "language": "python",
            "path": "/srv/generated/sdk/python_client.py", "code": "x = 1\n",
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-export", "sdk", "--dashboard-url", dashboard_url, "--json"],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["code"] == "x = 1\n"


def test_remote_export_sdk_command_reports_a_clean_error_when_no_schema_exported(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "No exported OpenAPI schema found. Run /api/export-openapi first."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-export", "sdk", "--dashboard-url", dashboard_url],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No exported OpenAPI schema found")


def test_remote_export_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-export", "openapi",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_remote_deploy_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "remote-deploy" in proc.stdout


def test_remote_deploy_command_reports_success(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "tag": "generated:latest", "pushed": False})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-deploy", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Built image 'generated:latest'" in proc.stdout
    assert "Pushed to the registry." not in proc.stdout
    assert handler.requests == ["/api/deploy"]
    assert json.loads(handler.bodies[0]) == {"push": False, "force": False}


def test_remote_deploy_command_passes_tag_push_platform_and_force_through(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "tag": "myapp:v1", "pushed": True})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-deploy", "--dashboard-url", dashboard_url,
            "--tag", "myapp:v1", "--push", "--platform", "linux/amd64", "--force",
        ],
        cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Built image 'myapp:v1'" in proc.stdout
    assert "Pushed to the registry." in proc.stdout
    assert json.loads(handler.bodies[0]) == {
        "push": True, "force": True, "tag": "myapp:v1", "platform": "linux/amd64",
    }


def test_remote_deploy_command_json_flag_emits_the_dashboards_own_response(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {"status": "success", "tag": "generated:latest", "pushed": False})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-deploy", "--dashboard-url", dashboard_url, "--json"], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["tag"] == "generated:latest"


def test_remote_deploy_command_reports_a_clean_error_when_stale_without_force(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(409, {
            "detail": (
                "The currently-compiled app no longer matches its source "
                "notebook's current content -- it was edited since the "
                'last compile. Run /api/compile again first, or pass '
                '"force": true to deploy the stale build anyway.'
            )
        })
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-deploy", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    _assert_clean_cli_error(proc, "no longer matches its source notebook")


def test_remote_deploy_command_reports_a_clean_error_when_nothing_is_compiled(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(404, {"detail": "No compiled app found. Run /api/compile first."})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-deploy", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    _assert_clean_cli_error(proc, "No compiled app found")


def test_remote_deploy_command_reports_a_clean_error_for_a_failed_build(
    tmp_path, fake_dashboard
):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(500, {"detail": "Docker build failed: some error"})
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["remote-deploy", "--dashboard-url", dashboard_url], cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Docker build failed")


def test_remote_deploy_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "remote-deploy",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")


def test_status_command_is_registered():

    proc = _run_cli(["--help"], cwd=Path.cwd())

    assert proc.returncode == 0
    assert "status" in proc.stdout


def test_status_command_prints_health_and_config(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "healthy", "service": "notebook-to-api",
            "compiled_app_present": True, "compiled_at": "2026-01-01T00:00:00+00:00",
        }),
        _json_response(200, {
            "status": "success",
            "max_upload_bytes": 10485760,
            "max_batch_upload_files": 50,
            "max_notebook_versions": 20,
            "max_tag_length": 40,
            "max_tags_per_notebook": 20,
            "deploy_subprocess_timeout_seconds": 600,
            "notebook_sort_keys": ["name", "size", "uploaded_at"],
            "notebook_sort_orders": ["asc", "desc"],
        }),
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(["status", "--dashboard-url", dashboard_url], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"Dashboard at {dashboard_url}: healthy" in proc.stdout
    assert "compiled app present, last compiled at 2026-01-01T00:00:00+00:00" in proc.stdout
    assert "max upload size: 10485760 bytes" in proc.stdout
    assert "max batch upload files: 50" in proc.stdout
    assert "notebook sort keys: name, size, uploaded_at" in proc.stdout
    assert handler.requests == ["/api/health", "/api/config"]


def test_status_command_reports_no_compiled_app(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "healthy", "service": "notebook-to-api",
            "compiled_app_present": False, "compiled_at": None,
        }),
        _json_response(200, {
            "status": "success", "max_upload_bytes": 1, "max_batch_upload_files": 1,
            "max_notebook_versions": 1, "max_tag_length": 1, "max_tags_per_notebook": 1,
            "deploy_subprocess_timeout_seconds": 1,
            "notebook_sort_keys": [], "notebook_sort_orders": [],
        }),
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(["status", "--dashboard-url", dashboard_url], cwd=workdir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no compiled app yet" in proc.stdout


def test_status_command_json_flag_emits_a_combined_result(tmp_path, fake_dashboard):

    dashboard_url, handler = fake_dashboard
    handler.responses = [
        _json_response(200, {
            "status": "healthy", "service": "notebook-to-api",
            "compiled_app_present": False, "compiled_at": None,
        }),
        _json_response(200, {
            "status": "success", "max_upload_bytes": 1, "max_batch_upload_files": 1,
            "max_notebook_versions": 1, "max_tag_length": 1, "max_tags_per_notebook": 1,
            "deploy_subprocess_timeout_seconds": 1,
            "notebook_sort_keys": [], "notebook_sort_orders": [],
        }),
    ]

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["status", "--dashboard-url", dashboard_url, "--json"], cwd=workdir,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["health"]["status"] == "healthy"
    assert data["config"]["max_upload_bytes"] == 1


def test_status_command_reports_a_clean_error_when_the_dashboard_is_unreachable(
    tmp_path,
):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        [
            "status",
            "--dashboard-url", "http://127.0.0.1:1", "--timeout", "5",
        ],
        cwd=workdir,
    )

    _assert_clean_cli_error(proc, "Is it running?")
