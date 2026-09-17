from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from backend.observability.deployment_governance_audit_evidence_integrity import (
    GovernanceIntegrityAuditEvidenceManifest,
)
from backend.observability.deployment_governance_audit_export import (
    GovernanceIntegrityAuditEvidenceBundle,
    GovernanceIntegrityAuditEvidenceExportResult,
    GovernanceIntegrityAuditExportSummary,
)
from backend.observability.deployment_governance_audit_export_cli import (
    GovernanceAuditExportExitCode,
    _render_export_failure,
    _render_export_human,
    run_deployment_governance_audit_export,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)


BASE_TIME = datetime(2026, 7, 15, 21, 0, 0, tzinfo=timezone.utc)


def make_export_result(
    *,
    record_count: int = 0,
    include_trend: bool = True,
    include_regression: bool = True,
    include_manifest: bool = True,
) -> GovernanceIntegrityAuditEvidenceExportResult:
    from backend.observability.deployment_governance_audit_regression import (
        detect_governance_integrity_regression,
    )
    from backend.observability.deployment_governance_audit_trends import (
        analyze_governance_integrity_audit_records,
    )

    records = tuple(
        GovernanceIntegrityAuditRecord(
            audit_id=f"audit-{index}",
            backend="sqlite",
            started_at=BASE_TIME + timedelta(minutes=index),
            completed_at=BASE_TIME + timedelta(minutes=index, seconds=2),
            outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
            total_records=1,
            valid_records=1,
            invalid_records=0,
            integrity_mismatches=0,
            missing_integrity_metadata=0,
            invalid_integrity_metadata=0,
            invalid_persisted_records=0,
        )
        for index in range(record_count)
    )

    bundle = GovernanceIntegrityAuditEvidenceBundle(
        schema_version=1,
        exported_at=BASE_TIME,
        record_count=record_count,
        records=records,
        summary=GovernanceIntegrityAuditExportSummary(
            total_audits=record_count,
            healthy_audits=record_count,
            unhealthy_audits=0,
            newest_audit_id=(
                None if not records else records[0].audit_id
            ),
            oldest_audit_id=(
                None if not records else records[-1].audit_id
            ),
        ),
        trend=(
            analyze_governance_integrity_audit_records(records)
            if include_trend
            else None
        ),
        regression=(
            detect_governance_integrity_regression(records)
            if include_regression
            else None
        ),
    )

    manifest = (
        GovernanceIntegrityAuditEvidenceManifest(
            schema_version=1,
            evidence_filename="evidence.json",
            hash_algorithm="sha256",
            sha256="a" * 64,
            byte_size=123,
            record_count=record_count,
            exported_at=BASE_TIME,
        )
        if include_manifest
        else None
    )

    return GovernanceIntegrityAuditEvidenceExportResult(
        bundle=bundle,
        evidence_path=Path("evidence.json"),
        manifest=manifest,
        manifest_path=(
            Path("evidence.json.manifest.json")
            if include_manifest
            else None
        ),
    )


def test_render_export_human_success() -> None:
    result = make_export_result(record_count=42)

    stdout = StringIO()

    _render_export_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Evidence Export" in output
    assert "Evidence: evidence.json" in output
    assert "Manifest: evidence.json.manifest.json" in output
    assert "Schema version: 1" in output
    assert "Records exported: 42" in output
    assert "Trend included: yes" in output
    assert "Regression included: yes" in output
    assert "SHA-256: " + "a" * 64 in output


def test_render_export_human_omitted_analysis() -> None:
    result = make_export_result(
        record_count=0, include_trend=False, include_regression=False
    )

    stdout = StringIO()

    _render_export_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Records exported: 0" in output
    assert "Trend included: no" in output
    assert "Regression included: no" in output


def test_render_export_human_disabled_manifest() -> None:
    result = make_export_result(record_count=1, include_manifest=False)

    stdout = StringIO()

    _render_export_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Manifest: disabled" in output
    assert "SHA-256:" not in output


def test_render_export_failure() -> None:
    stderr = StringIO()

    _render_export_failure(
        RuntimeError("simulated failure"), stderr=stderr
    )

    output = stderr.getvalue()

    assert "could not be completed" in output
    assert "simulated failure" in output


def test_runner_writes_evidence_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "export-runner.db"),
    )

    output_path = tmp_path / "evidence.json"

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_export(
        output_path=output_path, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert output_path.exists()
    assert "Records exported: 0" in stdout.getvalue()

    manifest_path = tmp_path / "evidence.json.manifest.json"
    assert manifest_path.exists()
    assert "SHA-256:" in stdout.getvalue()


def test_runner_refuses_to_overwrite_by_default(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "export-runner-overwrite.db"),
    )

    output_path = tmp_path / "evidence.json"
    output_path.write_text("{}", encoding="utf-8")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_export(
        output_path=output_path, stdout=StringIO(), stderr=stderr
    )

    assert exit_code == int(
        GovernanceAuditExportExitCode.EXECUTION_FAILED
    )
    assert "could not be completed" in stderr.getvalue()


def test_runner_force_overwrites(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "export-runner-force.db"),
    )

    output_path = tmp_path / "evidence.json"
    output_path.write_text("{}", encoding="utf-8")

    exit_code = run_deployment_governance_audit_export(
        output_path=output_path,
        force=True,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert exit_code == 0

    import json

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
