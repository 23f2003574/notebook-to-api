from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_reports_cli import (
    run_deployment_governance_audit_report_audits,
    run_deployment_governance_audit_report_collection,
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

    return runtime, record.audit_id


def test_report_audits_json_to_stdout(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "report-audits.db")

    create_audit(tmp_path, "report-audits.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_audits(
        title="Release v1",
        audit_ids=["audit-A"],
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["title"] == "Release v1"
    assert len(payload["audits"]) == 1


def test_report_audits_markdown_to_stdout(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "report-audits-md.db")

    create_audit(tmp_path, "report-audits-md.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_audits(
        title="Release v1",
        audit_ids=["audit-A"],
        report_format="md",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "# Release v1" in output
    assert "## Statistics" in output


def test_report_audits_missing_audit(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "report-audits-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_audits(
        title="Release v1",
        audit_ids=["missing"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_report_audits_writes_to_output_file(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "report-audits-output.db")

    create_audit(tmp_path, "report-audits-output.db")

    output_path = tmp_path / "report.json"

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_audits(
        title="Release v1",
        audit_ids=["audit-A"],
        output_path=str(output_path),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert output_path.exists()

    payload = json.loads(output_path.read_text())
    assert payload["title"] == "Release v1"

    summary = stdout.getvalue()
    assert "Report generated" in summary
    assert "Title: Release v1" in summary
    assert "Audits: 1" in summary
    assert "Health:" in summary


def test_report_collection_defaults_title_to_collection_name(
    monkeypatch, tmp_path,
) -> None:
    setup_env(monkeypatch, tmp_path, "report-collection.db")

    runtime, audit_id = create_audit(tmp_path, "report-collection.db")

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create("release-v1")
    collection_service.add("release-v1", audit_id)

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_report_collection(
        collection="release-v1", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["title"] == "release-v1"
    assert len(payload["audits"]) == 1


def test_report_collection_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "report-collection-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_report_collection(
        collection="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
