from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_audit_bookmarks_cli import (
    run_deployment_governance_audit_bookmark_add,
    run_deployment_governance_audit_bookmark_delete,
    run_deployment_governance_audit_bookmark_list,
    run_deployment_governance_audit_bookmark_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_audit(tmp_path, name: str) -> str:
    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )
    from backend.observability.deployment_governance_audit_history import (
        GovernanceIntegrityAuditOutcome,
        GovernanceIntegrityAuditRecord,
    )
    from datetime import datetime, timezone

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(tmp_path / name)
    )

    started_at = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

    record = GovernanceIntegrityAuditRecord(
        audit_id="audit-A",
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at,
        outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        total_records=10,
        valid_records=10,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )

    runtime.audit_history_repository.save(record)

    return record.audit_id


def test_add_creates_bookmark_by_audit_id(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-add.db")

    audit_id = create_audit(tmp_path, "bookmark-add.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_add(
        name="baseline",
        audit_id=audit_id,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Bookmark created" in stdout.getvalue()
    assert "Name: baseline" in stdout.getvalue()
    assert f"Audit: {audit_id}" in stdout.getvalue()


def test_add_uses_latest_by_default(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-add-latest.db")

    audit_id = create_audit(tmp_path, "bookmark-add-latest.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_add(
        name="baseline",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert f"Audit: {audit_id}" in stdout.getvalue()


def test_add_rejects_missing_audit(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-add-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_add(
        name="baseline",
        audit_id="missing",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_add_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-add-json.db")

    audit_id = create_audit(tmp_path, "bookmark-add-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_add(
        name="baseline",
        audit_id=audit_id,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "baseline"
    assert payload["audit_id"] == audit_id


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Bookmarks" in stdout.getvalue()
    assert "No governance audit bookmarks" in stdout.getvalue()


def test_list_populated(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-list-populated.db")

    audit_id = create_audit(tmp_path, "bookmark-list-populated.db")

    run_deployment_governance_audit_bookmark_add(
        name="baseline",
        audit_id=audit_id,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "baseline" in stdout.getvalue()


def test_show_existing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-show.db")

    audit_id = create_audit(tmp_path, "bookmark-show.db")

    run_deployment_governance_audit_bookmark_add(
        name="baseline",
        audit_id=audit_id,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_show(
        name="baseline", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Bookmark" in stdout.getvalue()
    assert "Name: baseline" in stdout.getvalue()
    assert f"Audit: {audit_id}" in stdout.getvalue()
    assert "Created:" in stdout.getvalue()


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_show(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_delete_existing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-delete.db")

    audit_id = create_audit(tmp_path, "bookmark-delete.db")

    run_deployment_governance_audit_bookmark_add(
        name="baseline",
        audit_id=audit_id,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_delete(
        name="baseline", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_exit_code = run_deployment_governance_audit_bookmark_show(
        name="baseline", stdout=StringIO(), stderr=StringIO()
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "bookmark-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_bookmark_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
