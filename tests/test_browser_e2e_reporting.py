"""Flaky browser tests are retried once in CI and leave traces behind; nothing is retried locally."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = (ROOT / "frontend" / "playwright.config.js").read_text()
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())


def test_playwright_retries_once_in_ci_only_and_records_traces():
    assert re.search(r"retries:\s*process\.env\.CI \? 1 : 0", CONFIG)
    assert "trace: 'on-first-retry'" in CONFIG
    assert "screenshot: 'only-on-failure'" in CONFIG
    assert "forbidOnly" in CONFIG  # a stray test.only must not silently skip the rest of the suite


def test_workflow_uploads_the_report_even_when_the_run_fails():
    steps = WORKFLOW["jobs"]["browser-e2e"]["steps"]
    upload = next(s for s in steps if s.get("name") == "Upload the browser test report")
    assert upload["if"] == "always()"
    assert re.search(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
    assert "frontend/playwright-report" in upload["with"]["path"]
    assert "frontend/test-results" in upload["with"]["path"]
    assert steps.index(upload) > max(i for i, s in enumerate(steps) if "npm run e2e" in s.get("run", ""))


def test_report_directories_are_not_tracked():
    ignored = (ROOT / "frontend" / ".gitignore").read_text() + (ROOT / ".gitignore").read_text()
    assert "playwright-report" in ignored and "test-results" in ignored
