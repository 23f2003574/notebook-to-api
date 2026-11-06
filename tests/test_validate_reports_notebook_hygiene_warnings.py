import subprocess
import sys
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parent.parent


def test_validate_prints_the_credential_and_mutable_default_warnings_without_echoing_secrets(tmp_path):
    """`compile` warned about literal credentials and mutable defaults but
    `validate`, the command CI runs, stayed silent about both."""
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"\n'
        "def f(x: int, bucket: list = []) -> list:\n"
        '    """doc"""\n    return bucket\n'
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "validate", str(path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )

    assert "Cell 1, line 1: API_KEY is assigned a literal credential" in proc.stdout
    assert "f: parameter 'bucket' has a mutable default" in proc.stdout
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in proc.stdout + proc.stderr


def test_validate_does_not_claim_no_issues_when_it_just_printed_hygiene_warnings(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        "def f(x: int, bucket: list = []) -> list:\n"
        '    """doc"""\n    return bucket\n'
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "validate", str(path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0
    assert "No issues found" not in proc.stdout
    assert "would still compile cleanly" in proc.stdout
