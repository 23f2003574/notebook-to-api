from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_saved_queries_cli import (
    run_deployment_governance_audit_saved_query_delete,
    run_deployment_governance_audit_saved_query_list,
    run_deployment_governance_audit_saved_query_run,
    run_deployment_governance_audit_saved_query_save,
    run_deployment_governance_audit_saved_query_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_audit(tmp_path, name: str, audit_id: str = "audit-A", healthy: bool = True) -> str:
    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(tmp_path / name)
    )

    started_at = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

    invalid_records = 0 if healthy else 1

    record = GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at,
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if healthy
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=10,
        valid_records=10 - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    runtime.audit_history_repository.save(record)

    return record.audit_id


def test_save_creates_query(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-save.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_save(
        name="healthy",
        healthy=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Saved Query" in stdout.getvalue()
    assert "Name: healthy" in stdout.getvalue()


def test_save_requires_at_least_one_filter(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-save-empty.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_save(
        name="empty", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_save_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-save-dup.db")

    run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True,
        stdout=StringIO(), stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True,
        stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == 2


def test_save_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-save-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True, json_output=True,
        stdout=stdout, stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "healthy"
    assert payload["query"]["healthy"] is True


def test_run_executes_saved_query(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-run.db")

    create_audit(tmp_path, "query-run.db", audit_id="A", healthy=True)

    run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True,
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_run(
        name="healthy", json_output=True,
        stdout=stdout, stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 1
    assert payload[0]["audit_id"] == "A"


def test_run_rejects_missing_query(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-run-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_run(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_list_saved_queries(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-list.db")

    run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True,
        stdout=StringIO(), stderr=StringIO(),
    )
    run_deployment_governance_audit_saved_query_save(
        name="baseline", label="baseline",
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Saved Queries" in output
    assert "healthy" in output
    assert "baseline" in output


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No governance audit queries" in stdout.getvalue()


def test_show_saved_query(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-show.db")

    run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True,
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_show(
        name="healthy", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Name: healthy" in stdout.getvalue()


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_show(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_delete_saved_query(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-delete.db")

    run_deployment_governance_audit_saved_query_save(
        name="healthy", healthy=True,
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_delete(
        name="healthy", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_exit_code = run_deployment_governance_audit_saved_query_show(
        name="healthy", stdout=StringIO(), stderr=StringIO()
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "query-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_saved_query_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
