from __future__ import annotations

import sys
from enum import IntEnum
from pathlib import Path
from typing import TextIO

from .deployment_governance_audit_export import (
    GovernanceIntegrityAuditEvidenceExportResult,
    GovernanceIntegrityAuditExportOptions,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


class GovernanceAuditExportExitCode(IntEnum):
    """
    Exit codes produced by the governance audit-history export command.
    """

    SUCCESS = 0

    EXECUTION_FAILED = 2


def run_deployment_governance_audit_export(
    *,
    output_path: str | Path,
    limit: int | None = None,
    include_trend: bool = True,
    include_regression: bool = True,
    trend_window: int = 20,
    create_manifest: bool = True,
    pretty: bool = True,
    force: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export a portable governance audit evidence
    bundle (plus, by default, a SHA-256 tamper-evidence manifest) to disk.

    This is the composition boundary: it reads environment configuration,
    builds the persistence runtime, and runs its export service. It never
    decides backend selection or database paths itself.

    Unlike `governance audits` and `governance audits prune`, this command
    writes its payload to a file rather than stdout: evidence bundles can
    be large, and stdout is reserved for a concise success summary so it
    stays readable in CI logs.
    """

    try:
        options = GovernanceIntegrityAuditExportOptions(
            limit=limit,
            include_trend=include_trend,
            include_regression=include_regression,
            trend_window=trend_window,
            create_manifest=create_manifest,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        result = (
            runtime
            .build_integrity_audit_export_service()
            .export_to_file(
                output_path,
                options=options,
                pretty=pretty,
                overwrite=force,
            )
        )

    except Exception as exc:
        _render_export_failure(exc, stderr=stderr)

        return int(GovernanceAuditExportExitCode.EXECUTION_FAILED)

    _render_export_human(result, stdout=stdout)

    return int(GovernanceAuditExportExitCode.SUCCESS)


def _render_export_human(
    result: GovernanceIntegrityAuditEvidenceExportResult,
    *,
    stdout: TextIO,
) -> None:
    bundle = result.bundle

    stdout.write("Governance Audit Evidence Export\n")

    stdout.write("================================\n\n")

    stdout.write(f"Evidence: {result.evidence_path}\n")

    stdout.write(
        "Manifest: "
        + (
            str(result.manifest_path)
            if result.manifest_path is not None
            else "disabled"
        )
        + "\n"
    )

    stdout.write("\n")

    stdout.write(f"Schema version: {bundle.schema_version}\n")

    stdout.write(f"Records exported: {bundle.record_count}\n")

    stdout.write(
        "Trend included: "
        + ("yes" if bundle.trend is not None else "no")
        + "\n"
    )

    stdout.write(
        "Regression included: "
        + ("yes" if bundle.regression is not None else "no")
        + "\n"
    )

    if result.manifest is not None:
        stdout.write(f"SHA-256: {result.manifest.sha256}\n")


def _render_export_failure(
    error: Exception,
    *,
    stderr: TextIO,
) -> None:
    stderr.write(
        "Governance audit evidence export could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
