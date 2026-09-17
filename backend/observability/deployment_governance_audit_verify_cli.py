from __future__ import annotations

import sys
from enum import IntEnum
from pathlib import Path
from typing import TextIO

from .deployment_governance_audit_evidence_integrity import (
    GovernanceIntegrityAuditEvidenceVerificationResult,
    default_governance_audit_evidence_manifest_path,
    load_governance_audit_evidence_manifest,
    verify_governance_audit_evidence,
)


class GovernanceAuditVerifyExitCode(IntEnum):
    """
    Exit codes produced by the governance audit-evidence verify command.

    EXECUTION_FAILED (the manifest could not be loaded, e.g. missing file
    or malformed/unsupported JSON) and VERIFICATION_FAILED (the manifest
    loaded fine but the evidence file does not match it) are distinct on
    purpose, mirroring the check/prune commands' "could not run" vs "ran
    and found a problem" distinction.
    """

    SUCCESS = 0

    EXECUTION_FAILED = 2

    VERIFICATION_FAILED = 3


def run_deployment_governance_audit_verify(
    *,
    evidence_path: str | Path,
    manifest_path: str | Path | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Verify a previously exported evidence file against its manifest.

    This is a pure file-based operation: unlike the other governance
    commands, it does not bootstrap a persistence runtime, because
    verification only concerns two files on disk and must work even after
    the originating database is gone.
    """

    resolved_evidence_path = Path(evidence_path)

    resolved_manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else default_governance_audit_evidence_manifest_path(
            resolved_evidence_path
        )
    )

    try:
        manifest = load_governance_audit_evidence_manifest(
            resolved_manifest_path
        )

    except Exception as exc:
        _render_verify_failure(exc, stderr=stderr)

        return int(GovernanceAuditVerifyExitCode.EXECUTION_FAILED)

    verification = verify_governance_audit_evidence(
        evidence_path=resolved_evidence_path, manifest=manifest
    )

    _render_verify_human(
        verification,
        evidence_path=resolved_evidence_path,
        manifest_path=resolved_manifest_path,
        stdout=stdout,
    )

    if verification.verified:
        return int(GovernanceAuditVerifyExitCode.SUCCESS)

    return int(GovernanceAuditVerifyExitCode.VERIFICATION_FAILED)


def _render_verify_human(
    verification: GovernanceIntegrityAuditEvidenceVerificationResult,
    *,
    evidence_path: Path,
    manifest_path: Path,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Audit Evidence Verification\n")

    stdout.write("======================================\n\n")

    stdout.write(f"Status: {verification.status.value.upper()}\n")

    stdout.write(f"Evidence: {evidence_path}\n")

    stdout.write(f"Manifest: {manifest_path}\n")

    if verification.verified:
        stdout.write(f"SHA-256: {verification.actual_sha256}\n")

        stdout.write(
            f"Byte size: {verification.actual_byte_size}\n"
        )

        stdout.write(
            "\nEvidence integrity verification passed.\n"
        )

        return

    stdout.write(
        f"\nExpected SHA-256: {verification.expected_sha256}\n"
    )

    stdout.write(
        "Actual SHA-256:   "
        + (
            "not available"
            if verification.actual_sha256 is None
            else verification.actual_sha256
        )
        + "\n"
    )

    stdout.write(
        f"\nExpected byte size: {verification.expected_byte_size}\n"
    )

    stdout.write(
        "Actual byte size:   "
        + (
            "not available"
            if verification.actual_byte_size is None
            else str(verification.actual_byte_size)
        )
        + "\n"
    )

    stdout.write(
        "\nEvidence integrity verification failed.\n"
    )


def _render_verify_failure(
    error: Exception,
    *,
    stderr: TextIO,
) -> None:
    stderr.write(
        "Governance audit evidence verification could not be "
        "executed.\n"
    )

    stderr.write(f"Reason: {error}\n")
