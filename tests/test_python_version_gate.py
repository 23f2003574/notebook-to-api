"""The Python versions the project claims, the CI matrix and the running interpreter agree."""
import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def _declared():
    return {m.group(1) for c in PROJECT["classifiers"] if (m := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", c))}


def _matrix():
    return {str(v) for v in WORKFLOW["jobs"]["test"]["strategy"]["matrix"]["python-version"]}


def _minor(version):
    return tuple(int(x) for x in version.split("."))


def test_the_complete_suite_runs_on_every_declared_version():
    assert _declared() == _matrix()


def test_the_matrix_does_not_hide_failures_in_one_version():
    assert WORKFLOW["jobs"]["test"]["strategy"].get("fail-fast") is False  # a 3.13 failure must not cancel 3.11's verdict


def test_the_matrix_step_proves_which_interpreter_ran():
    steps = " ".join(s.get("run", "") for s in WORKFLOW["jobs"]["test"]["steps"])
    assert "${{ matrix.python-version }}" in steps and "assert v ==" in steps


def test_every_other_job_uses_a_declared_version():
    for name, job in WORKFLOW["jobs"].items():
        for step in job["steps"]:
            version = step.get("with", {}).get("python-version") if "setup-python" in step.get("uses", "") else None
            if version and "matrix" not in str(version):
                assert str(version) in _declared(), (name, version)


def test_requires_python_matches_the_oldest_declared_version():
    minimum = _minor(PROJECT["requires-python"].lstrip(">="))
    assert minimum == min(_minor(v) for v in _declared())


def test_this_interpreter_is_a_supported_one():
    assert sys.version_info[:2] >= _minor(PROJECT["requires-python"].lstrip(">="))
