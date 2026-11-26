"""Silently shadowed definitions are a lint error: the later one wins and the earlier is dead."""
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ruff_is_configured_to_select_f811():
    select = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]["lint"]["select"]
    assert "F811" in select


def test_ruff_reports_a_redefined_function(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("def twice():\n    return 1\n\n\ndef twice():\n    return 2\n")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "F811", str(sample)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1 and "F811" in result.stdout


def test_no_shadowed_definitions_remain_in_backend_or_tests():
    result = subprocess.run([sys.executable, "-m", "ruff", "check", "backend", "tests"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
