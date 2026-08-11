import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_export_sdk_command_rejects_invalid_language_choice(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(["export-sdk", "--language", "rust"], cwd=workdir)

    assert proc.returncode != 0
    assert "invalid choice: 'rust'" in proc.stderr


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
