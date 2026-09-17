from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_evidence_integrity import (
    build_governance_audit_evidence_manifest,
    default_governance_audit_evidence_manifest_path,
    write_governance_audit_evidence_manifest,
)
from backend.observability.deployment_governance_audit_verify_cli import (
    GovernanceAuditVerifyExitCode,
    run_deployment_governance_audit_verify,
)


BASE_TIME = datetime(2026, 7, 15, 22, 0, 0, tzinfo=timezone.utc)


def write_evidence_and_manifest(tmp_path, *, content: bytes = b"payload"):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(content)

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=BASE_TIME,
    )

    manifest_path = default_governance_audit_evidence_manifest_path(
        evidence_path
    )

    write_governance_audit_evidence_manifest(manifest, manifest_path)

    return evidence_path, manifest_path


def test_verify_passes_for_untouched_evidence(tmp_path) -> None:
    evidence_path, _ = write_evidence_and_manifest(tmp_path)

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_verify(
        evidence_path=evidence_path, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    output = stdout.getvalue()

    assert "Status: VERIFIED" in output
    assert "Evidence integrity verification passed." in output


def test_verify_uses_default_manifest_path_when_not_specified(
    tmp_path,
) -> None:
    evidence_path, manifest_path = write_evidence_and_manifest(tmp_path)

    stdout = StringIO()

    run_deployment_governance_audit_verify(
        evidence_path=evidence_path, stdout=stdout, stderr=StringIO()
    )

    assert str(manifest_path) in stdout.getvalue()


def test_verify_detects_tampering(tmp_path) -> None:
    evidence_path, manifest_path = write_evidence_and_manifest(tmp_path)

    evidence_path.write_bytes(b"corrupt")  # same length as "payload"

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_verify(
        evidence_path=evidence_path,
        manifest_path=manifest_path,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == int(
        GovernanceAuditVerifyExitCode.VERIFICATION_FAILED
    )

    output = stdout.getvalue()

    assert "Status: DIGEST_MISMATCH" in output
    assert "Evidence integrity verification failed." in output


def test_verify_reports_missing_manifest_as_execution_failure(
    tmp_path,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_verify(
        evidence_path=evidence_path,
        manifest_path=tmp_path / "does-not-exist.manifest.json",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == int(
        GovernanceAuditVerifyExitCode.EXECUTION_FAILED
    )

    assert "could not be executed" in stderr.getvalue()


def test_verify_reports_missing_evidence_file(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=BASE_TIME,
    )

    manifest_path = default_governance_audit_evidence_manifest_path(
        evidence_path
    )

    write_governance_audit_evidence_manifest(manifest, manifest_path)

    evidence_path.unlink()

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_verify(
        evidence_path=evidence_path,
        manifest_path=manifest_path,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == int(
        GovernanceAuditVerifyExitCode.VERIFICATION_FAILED
    )

    assert "Status: EVIDENCE_FILE_MISSING" in stdout.getvalue()
