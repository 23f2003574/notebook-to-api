from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_prune_cli import (
    GovernanceAuditPruneExitCode,
    _render_prune_failure,
    _render_prune_human,
    _render_prune_json,
    run_deployment_governance_audit_prune,
)
from backend.observability.deployment_governance_audit_retention import (
    GovernanceIntegrityAuditPruningPlan,
    GovernanceIntegrityAuditPruningResult,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(2026, 7, 15, 20, 0, 0, tzinfo=timezone.utc)


def make_audit_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
) -> GovernanceIntegrityAuditRecord:
    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        total_records=10,
        valid_records=10,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def make_plan(
    *,
    total_records: int,
    retained_ids: tuple[str, ...],
    prunable_ids: tuple[str, ...],
) -> GovernanceIntegrityAuditPruningPlan:
    return GovernanceIntegrityAuditPruningPlan(
        evaluated_at=BASE_TIME,
        total_records=total_records,
        retained_records=len(retained_ids),
        prunable_records=len(prunable_ids),
        retained_audit_ids=retained_ids,
        prunable_audit_ids=prunable_ids,
        oldest_retained_started_at=BASE_TIME if retained_ids else None,
        newest_retained_started_at=BASE_TIME if retained_ids else None,
    )


def test_prune_human_output_dry_run_with_prunable_records() -> None:
    plan = make_plan(
        total_records=5,
        retained_ids=("a", "b"),
        prunable_ids=("c", "d", "e"),
    )

    result = GovernanceIntegrityAuditPruningResult(
        plan=plan, applied=False, deleted_records=0
    )

    stdout = StringIO()

    _render_prune_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Integrity Audit Retention" in output
    assert "Mode: DRY RUN" in output
    assert "Total records: 5" in output
    assert "Retained records: 2" in output
    assert "Prunable records: 3" in output
    assert "Deleted records: 0" in output
    assert "Run again with --apply" in output


def test_prune_human_output_applied() -> None:
    plan = make_plan(
        total_records=5,
        retained_ids=("a", "b"),
        prunable_ids=("c", "d", "e"),
    )

    result = GovernanceIntegrityAuditPruningResult(
        plan=plan, applied=True, deleted_records=3
    )

    stdout = StringIO()

    _render_prune_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Mode: APPLIED" in output
    assert "Total records before pruning: 5" in output
    assert "Deleted records: 3" in output
    assert "Run again with --apply" not in output


def test_prune_human_output_nothing_prunable() -> None:
    plan = make_plan(
        total_records=2,
        retained_ids=("a", "b"),
        prunable_ids=(),
    )

    result = GovernanceIntegrityAuditPruningResult(
        plan=plan, applied=False, deleted_records=0
    )

    stdout = StringIO()

    _render_prune_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert (
        "No audit records currently violate the retention policy."
        in output
    )
    assert "Run again with --apply" not in output


def test_prune_json_output_is_valid_json() -> None:
    plan = make_plan(
        total_records=3,
        retained_ids=("a",),
        prunable_ids=("b", "c"),
    )

    result = GovernanceIntegrityAuditPruningResult(
        plan=plan, applied=False, deleted_records=0
    )

    stdout = StringIO()

    _render_prune_json(result, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert payload["applied"] is False
    assert payload["deleted_records"] == 0
    assert payload["plan"]["total_records"] == 3
    assert payload["plan"]["prunable_audit_ids"] == ["b", "c"]


def test_render_prune_failure_human() -> None:
    stderr = StringIO()

    _render_prune_failure(
        RuntimeError("simulated failure"),
        json_output=False,
        stderr=stderr,
    )

    output = stderr.getvalue()

    assert "could not be evaluated" in output
    assert "simulated failure" in output


def test_render_prune_failure_json() -> None:
    stderr = StringIO()

    _render_prune_failure(
        RuntimeError("simulated failure"),
        json_output=True,
        stderr=stderr,
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["error"] == "simulated failure"
    assert payload["exit_code"] == int(
        GovernanceAuditPruneExitCode.EXECUTION_FAILED
    )


def test_runner_rejects_missing_retention_limits(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "prune-no-limits.db"),
    )

    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_deployment_governance_audit_prune(
        stdout=stdout, stderr=stderr
    )

    assert exit_code == int(
        GovernanceAuditPruneExitCode.EXECUTION_FAILED
    )
    assert "at least one retention limit" in stderr.getvalue()


def test_runner_dry_run_does_not_delete(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "prune-runner-dry-run.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    for index in range(5):
        runtime.audit_history_repository.save(
            make_audit_record(
                audit_id=f"audit-{index}", offset_minutes=index
            )
        )

    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(database_path)
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_prune(
        max_records=2, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Mode: DRY RUN" in stdout.getvalue()
    assert runtime.audit_history_repository.count() == 5


def test_runner_apply_deletes_planned_records(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "prune-runner-apply.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    for index in range(5):
        runtime.audit_history_repository.save(
            make_audit_record(
                audit_id=f"audit-{index}", offset_minutes=index
            )
        )

    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(database_path)
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_prune(
        max_records=2, apply=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Mode: APPLIED" in stdout.getvalue()
    assert "Deleted records: 3" in stdout.getvalue()

    recreated_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    assert recreated_runtime.audit_history_repository.count() == 2
