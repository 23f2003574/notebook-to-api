from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_audit_replay_diff import (
    GovernanceIntegrityAuditFieldDiff,
    GovernanceIntegrityAuditReplayDiff,
)
from backend.observability.deployment_governance_audit_replay_diff_cli import (
    _render_diff_failure,
    _render_diff_human,
    _render_diff_json,
    run_deployment_governance_audit_diff,
)


def make_diff(
    *,
    changed: bool,
    field_diffs: tuple[GovernanceIntegrityAuditFieldDiff, ...] = (),
) -> GovernanceIntegrityAuditReplayDiff:
    return GovernanceIntegrityAuditReplayDiff(
        previous_audit_id="audit-1",
        current_audit_id="audit-2",
        changed=changed,
        field_diffs=field_diffs,
    )


def test_render_diff_human_no_changes() -> None:
    diff = make_diff(changed=False)

    stdout = StringIO()

    _render_diff_human(diff, stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Diff" in output
    assert "Previous: audit-1" in output
    assert "Current: audit-2" in output
    assert "No operational differences detected." in output
    assert "Changed Fields" not in output


def test_render_diff_human_with_changes() -> None:
    diff = make_diff(
        changed=True,
        field_diffs=(
            GovernanceIntegrityAuditFieldDiff(
                field="invalid_records", previous=2, current=5
            ),
            GovernanceIntegrityAuditFieldDiff(
                field="integrity_mismatches", previous=1, current=4
            ),
        ),
    )

    stdout = StringIO()

    _render_diff_human(diff, stdout=stdout)

    output = stdout.getvalue()

    assert "Changed Fields" in output
    assert "invalid_records:" in output
    assert "2 -> 5" in output
    assert "integrity_mismatches:" in output
    assert "1 -> 4" in output


def test_render_diff_json() -> None:
    diff = make_diff(
        changed=True,
        field_diffs=(
            GovernanceIntegrityAuditFieldDiff(
                field="invalid_records", previous=2, current=5
            ),
        ),
    )

    stdout = StringIO()

    _render_diff_json(diff, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert payload["changed"] is True
    assert payload["field_diffs"] == [
        {"field": "invalid_records", "previous": 2, "current": 5}
    ]


def test_render_diff_failure_human() -> None:
    stderr = StringIO()

    _render_diff_failure(
        KeyError("missing"), json_output=False, stderr=stderr
    )

    output = stderr.getvalue()

    assert "could not be completed" in output


def test_render_diff_failure_json() -> None:
    stderr = StringIO()

    _render_diff_failure(
        LookupError("insufficient history"),
        json_output=True,
        stderr=stderr,
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["exit_code"] == 2


def test_runner_compares_by_audit_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "diff-runner-by-id.db"),
    )

    from tests.test_deployment_governance_audit_replay_diff import (
        make_record,
    )
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.audit_history_repository.save(
        make_record(audit_id="A", integrity_mismatches=0)
    )
    runtime.audit_history_repository.save(
        make_record(
            audit_id="B", offset_minutes=10, integrity_mismatches=1
        )
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_diff(
        previous_audit_id="A",
        current_audit_id="B",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Previous: A" in stdout.getvalue()
    assert "Current: B" in stdout.getvalue()


def test_runner_defaults_to_latest_pair(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "diff-runner-latest.db"),
    )

    from tests.test_deployment_governance_audit_replay_diff import (
        make_record,
    )
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.audit_history_repository.save(make_record(audit_id="A"))
    runtime.audit_history_repository.save(
        make_record(audit_id="B", offset_minutes=10)
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_diff(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Previous: A" in stdout.getvalue()
    assert "Current: B" in stdout.getvalue()


def test_runner_handles_insufficient_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "diff-runner-insufficient.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_diff(
        stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_runner_rejects_missing_audit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "diff-runner-missing.db"),
    )

    from tests.test_deployment_governance_audit_replay_diff import (
        make_record,
    )
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.audit_history_repository.save(make_record(audit_id="A"))

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_diff(
        previous_audit_id="missing",
        current_audit_id="A",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
