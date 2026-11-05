"""A leading underscore does not make a function private: compile must say so
instead of silently publishing `_helper` as `POST /_helper`."""
import os
import subprocess
import sys
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parent.parent


def _compile(tmp_path, source):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(source)]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(path),
         "--output", str(tmp_path / "gen_underscore_app")],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )


def test_underscore_function_is_flagged_with_the_private_directive_hint(tmp_path):
    proc = _compile(
        tmp_path,
        "def _helper(a: int = 1) -> int:\n    '''d'''\n    return a\n"
        "def public(a: int = 1) -> int:\n    '''d'''\n    return a\n",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "leading underscore" in out and "  _helper" in out
    assert "notebook-to-api: private" in out
    assert "  public" not in out.split("leading underscore")[1]


def test_no_warning_without_underscore_functions(tmp_path):
    proc = _compile(tmp_path, "def public(a: int = 1) -> int:\n    '''d'''\n    return a\n")
    assert "leading underscore" not in proc.stdout + proc.stderr
