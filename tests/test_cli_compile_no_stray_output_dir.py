"""Regression: a failed `compile` (missing notebook, non-notebook file, bad
output name) must not leave an empty --output directory behind."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cli(tmp_path, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )


def test_missing_notebook_leaves_no_output_dir(tmp_path):
    r = _cli(tmp_path, "compile", "typo.ipynb", "--output", "out")
    assert r.returncode == 1
    assert "typo.ipynb" in r.stdout + r.stderr
    assert not (tmp_path / "out").exists()


def test_non_json_notebook_leaves_no_output_dir(tmp_path):
    (tmp_path / "bad.ipynb").write_text("{bad")
    r = _cli(tmp_path, "compile", "bad.ipynb", "--output", "out")
    assert r.returncode == 1
    assert "not valid JSON" in r.stdout + r.stderr
    assert not (tmp_path / "out").exists()


def test_invalid_output_name_leaves_no_output_dir(tmp_path):
    (tmp_path / "bad.ipynb").write_text("{bad")
    r = _cli(tmp_path, "compile", "bad.ipynb", "--output", "my-app!")
    assert r.returncode == 1
    assert not (tmp_path / "my-app!").exists()


def test_valid_compile_still_creates_output_dir(tmp_path):
    import nbformat

    nb = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell("def add(a: int, b: int):\n    return a + b")]
    )
    nbformat.write(nb, str(tmp_path / "ok.ipynb"))
    r = _cli(tmp_path, "compile", "ok.ipynb", "--output", "out")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "out" / "app.py").is_file()


def test_output_inside_a_file_reports_a_clear_error(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "ok.ipynb").write_text("{}")
    r = _cli(tmp_path, "compile", "ok.ipynb", "--output", "notes.txt/app")
    out = r.stdout + r.stderr
    assert r.returncode == 1
    assert "is a file, not a directory" in out
    assert "Errno" not in out
