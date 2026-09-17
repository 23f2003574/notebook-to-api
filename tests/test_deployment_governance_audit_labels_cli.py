from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_labels_cli import (
    run_deployment_governance_audit_label_add,
    run_deployment_governance_audit_label_list,
    run_deployment_governance_audit_label_remove,
    run_deployment_governance_audit_label_search,
    run_deployment_governance_audit_label_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_audit(tmp_path, name: str, audit_id: str = "audit-A") -> str:
    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(tmp_path / name)
    )

    started_at = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

    record = GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
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


def test_add_creates_label(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-add.db")

    audit_id = create_audit(tmp_path, "label-add.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_add(
        audit_id=audit_id,
        label="release",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Label added" in stdout.getvalue()
    assert f"Audit: {audit_id}" in stdout.getvalue()
    assert "Label: release" in stdout.getvalue()


def test_add_rejects_missing_audit(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-add-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_label_add(
        audit_id="missing",
        label="release",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_add_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-add-dup.db")

    audit_id = create_audit(tmp_path, "label-add-dup.db")

    run_deployment_governance_audit_label_add(
        audit_id=audit_id,
        label="release",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_label_add(
        audit_id=audit_id,
        label="release",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_add_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-add-json.db")

    audit_id = create_audit(tmp_path, "label-add-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_add(
        audit_id=audit_id,
        label="release",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["audit_id"] == audit_id
    assert payload["label"] == "release"


def test_show_labels(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-show.db")

    audit_id = create_audit(tmp_path, "label-show.db")

    run_deployment_governance_audit_label_add(
        audit_id=audit_id, label="release",
        stdout=StringIO(), stderr=StringIO(),
    )
    run_deployment_governance_audit_label_add(
        audit_id=audit_id, label="baseline",
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_show(
        audit_id=audit_id, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Audit Labels" in stdout.getvalue()
    assert "release" in stdout.getvalue()
    assert "baseline" in stdout.getvalue()


def test_show_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-show-empty.db")

    audit_id = create_audit(tmp_path, "label-show-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_show(
        audit_id=audit_id, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No labels have been applied" in stdout.getvalue()


def test_search_returns_matching_audits(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-search.db")

    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "label-search.db"
        )
    )

    started_at = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

    for audit_id in ("audit-A", "audit-B", "audit-C"):
        runtime.audit_history_repository.save(
            GovernanceIntegrityAuditRecord(
                audit_id=audit_id,
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
        )

    service = runtime.build_integrity_audit_label_service()
    service.add("audit-A", "release")
    service.add("audit-B", "baseline")
    service.add("audit-C", "release")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_search(
        label="release", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "audit-A" in output
    assert "audit-C" in output
    assert "audit-B" not in output


def test_remove_label(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-remove.db")

    audit_id = create_audit(tmp_path, "label-remove.db")

    run_deployment_governance_audit_label_add(
        audit_id=audit_id, label="release",
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_remove(
        audit_id=audit_id, label="release",
        stdout=stdout, stderr=StringIO(),
    )

    assert exit_code == 0
    assert "removed" in stdout.getvalue()


def test_remove_missing_label(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-remove-missing.db")

    audit_id = create_audit(tmp_path, "label-remove-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_label_remove(
        audit_id=audit_id, label="missing",
        stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == 2


def test_list_all_labels(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-list.db")

    audit_id = create_audit(tmp_path, "label-list.db")

    run_deployment_governance_audit_label_add(
        audit_id=audit_id, label="release",
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "release" in stdout.getvalue()


def test_list_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "label-list-json.db")

    audit_id = create_audit(tmp_path, "label-list-json.db")

    run_deployment_governance_audit_label_add(
        audit_id=audit_id, label="release",
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_label_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert isinstance(payload, list)
    assert payload[0]["audit_id"] == audit_id
    assert payload[0]["label"] == "release"
