"""compile --require-pinned fails when requirements.txt would not be reproducible."""
import subprocess
import sys
from pathlib import Path

import nbformat

from backend.compiler import unpinned_requirements

ROOT = Path(__file__).resolve().parent.parent


def _compile(tmp_path, source, *flags):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(source)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(tmp_path / "nb.ipynb"), "--output", str(tmp_path / "out"), *flags],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )


def test_unpinned_requirements_lists_only_entries_without_a_version(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("# comment\nfastapi==0.1\n\nnot_installed_pkg\nother>=2\ngit+https://x/y.git@v1\nbare_two\n")
    assert unpinned_requirements(str(path)) == ["not_installed_pkg", "bare_two"]


def test_fully_pinned_project_passes_the_gate(tmp_path):
    result = _compile(tmp_path, "def add(a: int) -> int:\n    return a\n", "--require-pinned")
    assert result.returncode == 0, result.stdout + result.stderr
    assert unpinned_requirements(str(tmp_path / "out" / "requirements.txt")) == []


def test_uninstalled_dependency_fails_the_gate_but_still_writes_the_app(tmp_path):
    source = "import zzz_not_a_real_pkg\n\ndef add(a: int) -> int:\n    return a\n"
    plain = _compile(tmp_path, source)
    assert plain.returncode == 0  # default behavior unchanged: warns, does not fail
    gated = _compile(tmp_path, source, "--require-pinned")
    assert gated.returncode == 1
    assert "zzz-not-a-real-pkg" in gated.stdout.replace("_", "-") and "--require-pinned" in gated.stdout
    assert (tmp_path / "out" / "app.py").exists()
