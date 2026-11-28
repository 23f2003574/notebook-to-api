"""Coverage of the security-critical modules is measured in CI and cannot silently drop."""
import json
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


FLOORS = json.loads((ROOT / "coverage_floors.json").read_text())


def test_every_measured_module_has_a_floor_and_no_floor_is_trivial():
    assert set(CONFIG["report"]["include"]) == set(FLOORS)  # a module cannot join or leave the gate unnoticed
    assert all(60 <= floor <= 100 for floor in FLOORS.values())
    assert "backend/dashboard_compile_slots.py" in FLOORS


def test_the_floors_are_checked_in_ci_after_the_coverage_run():
    names = [step.get("name") for step in WORKFLOW["jobs"]["test"]["steps"]]
    check = STEPS["Check the per-file coverage floors"]
    assert names.index("Check the per-file coverage floors") > names.index("Run tests with coverage")
    assert "scripts/check_coverage_floors.py" in check["run"] and check["if"] == "matrix.python-version == '3.13'"


def _report(**percents):
    return {"files": {path: {"summary": {"percent_covered": value}} for path, value in percents.items()}}


def test_a_reduced_module_fails_a_compliant_run_passes_and_a_missing_one_fails(tmp_path):
    from scripts.check_coverage_floors import check, main

    full = {path: float(floor) + 0.5 for path, floor in FLOORS.items()}
    assert check({"files": {p: {"summary": {"percent_covered": v}} for p, v in full.items()}}, FLOORS) == []
    reduced = dict(full, **{"backend/dashboard_compile_slots.py": FLOORS["backend/dashboard_compile_slots.py"] - 0.01})
    problems = check({"files": {p: {"summary": {"percent_covered": v}} for p, v in reduced.items()}}, FLOORS)
    assert len(problems) == 1 and "dashboard_compile_slots" in problems[0]
    dropped = {p: v for p, v in full.items() if p != "backend/dashboard_keys.py"}
    assert any("not measured" in p for p in check({"files": {p: {"summary": {"percent_covered": v}} for p, v in dropped.items()}}, FLOORS))
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"files": {p: {"summary": {"percent_covered": 100.0}} for p in FLOORS}}))
    assert main(["x", str(report)]) == 0
    report.write_text(json.dumps({"files": {}}))
    assert main(["x", str(report)]) == 1
