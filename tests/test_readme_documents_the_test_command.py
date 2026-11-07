import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_documents_the_pytest_command_ci_runs_and_the_files_it_names_exist():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    section = readme.split("## Running the Tests")[1].split("\n## ")[0]

    assert re.search(r"^\s+pytest\s*$", ci, re.M), "CI no longer runs bare pytest"
    assert ".venv/bin/python -m pytest\n" in section
    assert "requirements-dev.txt" in readme
    assert "pytest" in (ROOT / "requirements-dev.txt").read_text()
    for name in re.findall(r"pytest (tests/\S+\.py)", section):
        assert (ROOT / name).is_file(), name
