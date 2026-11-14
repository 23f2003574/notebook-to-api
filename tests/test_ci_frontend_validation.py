"""The frontend CI job lints, tests and builds using the package.json scripts."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
SCRIPTS = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["scripts"]


def _steps():
    return WORKFLOW["jobs"]["frontend"]["steps"]


def _runs():
    return [s["run"] for s in _steps() if "run" in s]


def test_installs_from_the_lockfile_then_lints_tests_and_builds():
    runs = _runs()
    assert runs == ["npm ci", "npm run lint", "npm test", "npm run build"]
    assert (ROOT / "frontend" / "package-lock.json").exists()


def test_every_invoked_script_exists_and_runs_in_the_frontend_directory():
    for step in _steps():
        if step.get("run", "").startswith("npm"):
            assert step["working-directory"] == "frontend"
    for script in ("lint", "test", "build"):
        assert script in SCRIPTS


def test_no_step_can_swallow_a_failure():
    for step in _steps():
        assert step.get("continue-on-error") is not True
        assert "|| true" not in step.get("run", "")
    assert WORKFLOW["jobs"]["frontend"].get("continue-on-error") is not True


def test_other_jobs_are_still_present():
    assert {"test", "docker-smoke", "frontend"} <= set(WORKFLOW["jobs"])
