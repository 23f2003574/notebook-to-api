"""Every workflow must declare the GITHUB_TOKEN permissions it needs, read-only unless justified."""
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.y*ml"))
# job-level write permissions that are allowed, as {(workflow file, job): {permission, ...}}
ALLOWED_WRITES: dict = {}


def _load(path):
    return yaml.safe_load(path.read_text())


def violations(workflow, name="workflow.yml"):
    """Problems in one parsed workflow: a missing top-level block, or write access not allowed."""
    found = []
    top = workflow.get("permissions")
    if top is None:
        found.append("no top-level 'permissions:' block (the token inherits the repository default)")
    scopes = [("<top>", top)] + [(job, body.get("permissions")) for job, body in (workflow.get("jobs") or {}).items()]
    for job, perms in scopes:
        if perms in (None, {}):
            continue
        if isinstance(perms, str):
            if perms != "read-all":
                found.append(f"{job}: permissions '{perms}' is broader than read access")
            continue
        for scope, level in perms.items():
            if level == "write" and scope not in ALLOWED_WRITES.get((name, job), set()):
                found.append(f"{job}: '{scope}: write' is not allowed")
    return found


def test_workflows_exist():
    assert WORKFLOWS


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_token_is_least_privilege(path):
    assert violations(_load(path), path.name) == []


def test_the_check_detects_missing_and_broad_permissions():
    assert violations({"jobs": {"a": {}}})
    assert violations({"permissions": "write-all", "jobs": {}})
    assert violations({"permissions": {"contents": "read"}, "jobs": {"a": {"permissions": {"contents": "write"}}}})
    assert violations({"permissions": {"contents": "read"}, "jobs": {"a": {}}}) == []
