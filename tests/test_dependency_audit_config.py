"""Known-vulnerable dependencies fail CI, and Dependabot proposes the upgrades."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
DEPENDABOT = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
JOB = WORKFLOW["jobs"]["dependency-audit"]


def _scripts():
    return "\n".join(step.get("run", "") for step in JOB["steps"])


def test_the_audit_runs_on_pull_requests_and_pushes():
    assert {"push", "pull_request"} <= set(WORKFLOW.get(True, WORKFLOW.get("on")))


def test_python_audit_covers_the_constrained_runtime_dependencies():
    script = _scripts()
    assert "pip install --require-hashes -r requirements.lock" in script  # the audited set is the locked set
    assert "pip-audit==" in script and "pip-audit --path" in script


def test_npm_audit_checks_production_dependencies_at_high_severity():
    step = next(s for s in JOB["steps"] if "npm audit" in s.get("run", ""))
    assert step["working-directory"] == "frontend"
    assert "npm audit --omit=dev --audit-level=high" in step["run"]


def test_a_finding_cannot_be_ignored_by_the_workflow():
    assert "continue-on-error" not in JOB
    for step in JOB["steps"]:
        assert "continue-on-error" not in step
        assert "|| true" not in step.get("run", "")
        assert "--ignore-vuln" not in step.get("run", "")


def test_dependabot_covers_python_npm_and_actions():
    updates = {u["package-ecosystem"]: u["directory"] for u in DEPENDABOT["updates"]}
    assert updates == {"pip": "/", "npm": "/frontend", "github-actions": "/"}
    assert (ROOT / "frontend" / "package-lock.json").exists() and (ROOT / "requirements.txt").exists()
