import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")


def _usage_command():
    """The first fenced command of the README's Usage section."""
    usage = README.split("## Usage", 1)[1].split("##", 1)[0]
    block = re.search(r"```bash\n(.*?)```", usage, re.S).group(1)
    return next(line for line in block.splitlines() if line and not line.startswith("#"))


def test_readme_usage_command_compiles_and_writes_every_file_it_lists(tmp_path):
    """The Usage example pointed at `backend/compiler.py --notebook ...`, which
    has no command line at all; run the documented command for real and check
    each file the README says it generates."""
    command = _usage_command().replace(".venv/bin/python", sys.executable, 1)
    command = command.replace("notebooks/sample.ipynb", str(PROJECT_ROOT / "notebooks" / "sample.ipynb"))

    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    proc = subprocess.run(
        command.split(), cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    listed = re.findall(r"`(generated/[^`]+)`", README.split("## Usage", 1)[1].split("##", 1)[0])
    assert "generated/app.py" in listed and "generated/Dockerfile" in listed
    for path in listed:
        assert (tmp_path / path).is_file(), f"README lists {path} but compile did not write it"


def test_readme_states_a_minimum_python_that_matches_the_ci_matrix():
    """A new user had no way to know which Python versions are supported; the
    README's stated minimum must be the lowest version CI actually tests."""
    ci = (PROJECT_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[(.*?)\]", ci).group(1)
    versions = sorted(
        tuple(int(part) for part in v.strip(' "\'').split("."))
        for v in matrix.split(",")
    )
    lowest = ".".join(str(part) for part in versions[0])

    setup = README.split("## Setup", 1)[1].split("```", 1)[0]
    assert f"Python {lowest} or newer" in setup
