"""JUnit and Playwright results: failures and retry-only passes fail the run, and so does a missing report."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = str(ROOT / "scripts" / "check_test_results.py")
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())


def run(*args, env=None):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env)


def junit(tmp_path, body, name="r.xml"):
    path = tmp_path / name
    path.write_text(f'<testsuites><testsuite name="s">{body}</testsuite></testsuites>')
    return str(path)


def test_junit_clean_run_passes_and_is_summarised(tmp_path):
    summary = tmp_path / "summary.md"
    result = run("junit", junit(tmp_path, '<testcase classname="a" name="ok"/><testcase classname="a" name="s"><skipped/></testcase>'),
                 env={"GITHUB_STEP_SUMMARY": str(summary), "PATH": ""})
    assert result.returncode == 0 and "2 tests, 1 skipped, 0 failed, 0 passed only after a retry" in result.stdout
    assert "2 tests" in summary.read_text()


def test_junit_failure_and_error_fail_and_are_named(tmp_path):
    result = run("junit", junit(tmp_path, '<testcase classname="a" name="bad"><failure/></testcase>'
                                          '<testcase classname="a" name="boom"><error/></testcase>'))
    assert result.returncode == 1 and "FAILED: a::bad" in result.stdout and "FAILED: a::boom" in result.stdout


def test_junit_pass_only_after_a_retry_fails_the_run(tmp_path):
    result = run("junit", junit(tmp_path, '<testcase classname="a" name="flaky"><flakyFailure/></testcase>'
                                          '<testcase classname="a" name="again"><rerunFailure/></testcase>'))
    assert result.returncode == 1 and "PASSED ONLY ON RETRY: a::flaky" in result.stdout and "again" in result.stdout


def test_missing_empty_and_unparsable_reports_fail(tmp_path):
    assert run("junit", str(tmp_path / "none.xml")).returncode == 1
    assert run("junit").returncode == 1
    assert run("junit", junit(tmp_path, "")).returncode == 1  # no tests at all
    bad = tmp_path / "bad.xml"
    bad.write_text("<not xml")
    assert run("junit", str(bad)).returncode == 1
    assert run("playwright", str(tmp_path / "none.json")).returncode == 1


def _playwright(tmp_path, *statuses):
    specs = [{"title": f"t{i}", "tests": [{"status": status}]} for i, status in enumerate(statuses)]
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"suites": [{"title": "lifecycle.spec.js", "suites": [], "specs": specs}]}))
    return str(path)


def test_playwright_statuses(tmp_path):
    assert run("playwright", _playwright(tmp_path, "expected", "skipped")).returncode == 0
    flaky = run("playwright", _playwright(tmp_path, "expected", "flaky"))
    assert flaky.returncode == 1 and "PASSED ONLY ON RETRY: lifecycle.spec.js > t1" in flaky.stdout
    assert run("playwright", _playwright(tmp_path, "unexpected")).returncode == 1


def test_the_workflow_checks_results_even_when_a_test_step_fails():
    test_steps = {s.get("name"): s for s in WORKFLOW["jobs"]["test"]["steps"]}
    summary = test_steps["Summarise the test results"]
    assert summary["if"] == "always()" and "scripts/check_test_results.py junit" in summary["run"]
    assert test_steps["Upload the JUnit test results"]["if"] == "always()"
    assert "--junitxml" in test_steps["Run tests"]["run"] and "--junitxml" in test_steps["Run tests with coverage"]["run"]
    e2e = {s.get("name"): s for s in WORKFLOW["jobs"]["browser-e2e"]["steps"]}
    assert e2e["Check the browser test results"]["if"] == "always()"
    assert "playwright-results.json" in (ROOT / "frontend/playwright.config.js").read_text()
