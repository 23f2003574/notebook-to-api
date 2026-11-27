"""Coverage of the security-critical modules is measured in CI and cannot silently drop."""
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
STEPS = {step["name"]: step for step in WORKFLOW["jobs"]["test"]["steps"] if "name" in step}


def test_every_measured_module_exists_and_the_critical_ones_are_listed():
    listed = set(CONFIG["report"]["include"])
    assert all((ROOT / path).is_file() for path in listed)
    assert {
        "backend/dashboard.py", "backend/serve.py", "backend/runtime/isolated_execution.py",
        "backend/generator/api_generator.py", "backend/dashboard_keys.py", "backend/dashboard_rate_limit.py",
    } <= listed


def test_the_floor_is_a_real_threshold_below_the_recorded_measurement():
    assert 80 <= CONFIG["report"]["fail_under"] <= 96


def test_coverage_runs_on_one_matrix_leg_and_the_plain_run_covers_the_rest():
    coverage = STEPS["Run tests with coverage"]
    plain = STEPS["Run tests"]
    assert coverage["if"] == "matrix.python-version == '3.13'" and "--cov" in coverage["run"]
    assert plain["if"] == "matrix.python-version != '3.13'" and "--cov" not in plain["run"]


def test_the_report_is_uploaded_as_an_artifact_even_when_the_floor_fails():
    upload = STEPS["Upload the coverage report"]
    assert upload["if"].startswith("always()")
    assert upload["with"]["name"] == "coverage-report" and upload["with"]["path"] == "coverage.xml"


def test_pytest_cov_is_a_pinned_dev_dependency():
    assert "pytest-cov" in (ROOT / "requirements-dev.txt").read_text().split()
    assert "pytest-cov==" in (ROOT / "constraints.txt").read_text()
