from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_collections_cli import (
    run_deployment_governance_audit_collection_add,
    run_deployment_governance_audit_collection_create,
    run_deployment_governance_audit_collection_delete,
    run_deployment_governance_audit_collection_list,
    run_deployment_governance_audit_collection_remove,
    run_deployment_governance_audit_collection_show,
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


def test_create_collection(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Collection created" in stdout.getvalue()
    assert "Name: release-v1" in stdout.getvalue()


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-create-dup.db")

    run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_create_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_collection_create(
        name="release-v1",
        description="First release",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "release-v1"
    assert payload["description"] == "First release"
    assert payload["audits"] == []


def test_add_and_show(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-add-show.db")

    audit_id = create_audit(tmp_path, "collection-add-show.db")

    run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )

    add_exit_code = run_deployment_governance_audit_collection_add(
        name="release-v1", audit_id=audit_id,
        stdout=StringIO(), stderr=StringIO(),
    )

    assert add_exit_code == 0

    stdout = StringIO()

    show_exit_code = run_deployment_governance_audit_collection_show(
        name="release-v1", stdout=stdout, stderr=StringIO()
    )

    assert show_exit_code == 0
    output = stdout.getvalue()
    assert "Collection" in output
    assert "Name: release-v1" in output
    assert "Audits" in output
    assert audit_id in output


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_collection_show(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_add_rejects_missing_audit(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-add-missing.db")

    run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_collection_add(
        name="release-v1", audit_id="missing",
        stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == 2


def test_remove_audit(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-remove.db")

    audit_id = create_audit(tmp_path, "collection-remove.db")

    run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )
    run_deployment_governance_audit_collection_add(
        name="release-v1", audit_id=audit_id,
        stdout=StringIO(), stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_collection_remove(
        name="release-v1", audit_id=audit_id,
        stdout=stdout, stderr=StringIO(),
    )

    assert exit_code == 0
    assert "removed" in stdout.getvalue()


def test_list_collections(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-list.db")

    run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )
    run_deployment_governance_audit_collection_create(
        name="incident-42", stdout=StringIO(), stderr=StringIO()
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_collection_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Collections" in output
    assert "release-v1" in output
    assert "incident-42" in output


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_collection_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No governance audit collections" in stdout.getvalue()


def test_delete_collection(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-delete.db")

    run_deployment_governance_audit_collection_create(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_collection_delete(
        name="release-v1", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_exit_code = run_deployment_governance_audit_collection_show(
        name="release-v1", stdout=StringIO(), stderr=StringIO()
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "collection-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_collection_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
