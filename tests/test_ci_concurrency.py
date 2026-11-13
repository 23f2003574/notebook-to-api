"""Pushes to main must each get a completed CI run; only PR runs may be cancelled."""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"


def _concurrency():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["concurrency"]


def test_cancel_in_progress_is_limited_to_pull_requests():
    flag = _concurrency()["cancel-in-progress"]
    assert flag is not True  # an unconditional true cancels earlier main pushes
    assert flag == "${{ github.event_name == 'pull_request' }}"


def test_each_main_push_has_its_own_group_and_each_pr_shares_one():
    group = _concurrency()["group"]
    assert "github.workflow" in group
    # Non-PR events (pushes to main) are keyed by commit, so they never queue behind
    # or replace one another; PR runs are keyed by PR number so a new push supersedes.
    assert "github.sha" in group
    assert "github.event.pull_request.number" in group
    assert "github.ref" not in group
