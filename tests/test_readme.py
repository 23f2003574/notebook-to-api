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


def test_readme_dashboard_section_matches_the_start_script_and_package():
    """The dashboard had no mention in the README; keep the documented
    commands tied to what start-dashboard.sh and the frontend really run."""
    section = README.split("## Dashboard (optional)", 1)[1].split("##", 1)[0]
    script = (PROJECT_ROOT / "start-dashboard.sh").read_text(encoding="utf-8")
    package = (PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")

    assert "./start-dashboard.sh" in section
    assert "python -m backend.dashboard" in section and "python -m backend.dashboard" in script
    assert "npm run dev" in section and "npm run dev" in script and '"dev"' in package
    assert "if __name__" in (PROJECT_ROOT / "backend" / "dashboard.py").read_text(encoding="utf-8")


def test_documented_dashboard_port_is_the_port_the_dashboard_really_binds(monkeypatch):
    """README and start-dashboard.sh both said 8000 while the dashboard
    (and the frontend that calls it) use 8001; 8000 is the generated app's."""
    import re

    from backend.dashboard import dashboard_port

    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_PORT", raising=False)
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "start-dashboard.sh").read_text(encoding="utf-8")
    section = readme.split("## Dashboard")[1].split("\n## ")[0]

    assert f"default port {dashboard_port()}" in section
    assert f"Dashboard API Server on http://localhost:{dashboard_port()}" in script
    assert not re.search(r"default port 8000\b", section)
