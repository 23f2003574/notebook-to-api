"""CI runs the backend tests in parallel; shared-resource files stay serial and the setup is pinned."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys_conftest = (ROOT / "tests" / "conftest.py").read_text()
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())


def test_xdist_is_a_pinned_dev_dependency():
    assert re.search(r"^pytest-xdist\s*$", (ROOT / "requirements-dev.txt").read_text(), re.M)
    assert re.search(r"^pytest-xdist==\d", (ROOT / "constraints.txt").read_text(), re.M)


def test_ci_runs_both_test_legs_in_parallel_and_reports_slow_tests():
    runs = [s["run"] for s in WORKFLOW["jobs"]["test"]["steps"] if "pytest" in s.get("run", "")]
    assert len(runs) == 2  # the non-coverage leg and the coverage leg
    for run in runs:
        parallel = next(line for line in run.splitlines() if "-n auto" in line)
        assert "--dist loadgroup" in parallel and "--durations=25" in parallel
        assert "--ignore=tests/test_cli_remote_audit.py" in parallel
        assert "pytest tests/test_cli_remote_audit.py" in run  # still run, once, in a single process


def test_serial_files_exist_and_are_grouped():
    from tests.conftest import SERIAL_TEST_FILES  # noqa: PLC0415

    for name in SERIAL_TEST_FILES:
        assert list((ROOT / "tests").rglob(name)), f"{name} no longer exists; update SERIAL_TEST_FILES"
    assert "xdist_group" in sys_conftest


