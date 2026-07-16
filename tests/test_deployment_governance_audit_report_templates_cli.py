from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportSource,
)
from backend.observability.deployment_governance_audit_report_templates_cli import (
    run_deployment_governance_audit_report_template_create,
    run_deployment_governance_audit_report_template_delete,
    run_deployment_governance_audit_report_template_generate,
    run_deployment_governance_audit_report_template_list,
    run_deployment_governance_audit_report_template_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_audit_and_collection(
    tmp_path, name: str, audit_id: str = "audit-A",
    collection_name: str = "release-v1",
):
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

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create(collection_name)
    collection_service.add(collection_name, audit_id)

    return record.audit_id


def test_create_template(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-create.db")

    create_audit_and_collection(tmp_path, "template-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_template_create(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Template created" in stdout.getvalue()
    assert "Name: release" in stdout.getvalue()


def test_create_rejects_missing_source(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-create-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_template_create(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="missing",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_generate_from_template(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-generate.db")

    audit_id = create_audit_and_collection(
        tmp_path, "template-generate.db"
    )

    run_deployment_governance_audit_report_template_create(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_template_generate(
        name="release", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["title"] == "Release Report"
    assert payload["audits"][0]["audit_id"] == audit_id


def test_generate_missing_template(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-generate-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_template_generate(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_list_templates(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-list.db")

    create_audit_and_collection(tmp_path, "template-list.db")

    run_deployment_governance_audit_report_template_create(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_template_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "release" in stdout.getvalue()


def test_show_template(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-show.db")

    create_audit_and_collection(tmp_path, "template-show.db")

    run_deployment_governance_audit_report_template_create(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_template_show(
        name="release", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Name: release" in output
    assert "Title: Release Report" in output
    assert "Source: collection" in output


def test_delete_template(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "template-delete.db")

    create_audit_and_collection(tmp_path, "template-delete.db")

    run_deployment_governance_audit_report_template_create(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_template_delete(
        name="release", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_exit_code = run_deployment_governance_audit_report_template_show(
        name="release", stdout=StringIO(), stderr=StringIO()
    )

    assert show_exit_code == 2
