from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO

from .deployment_governance_audit_reports import (
    GovernanceIntegrityAuditReport,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_report_collection(
    *,
    collection: str,
    title: str | None = None,
    output_path: str | None = None,
    report_format: str = "json",
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and generate a report from every audit in a
    collection.

    This is a read-only inspection command: it never executes a new
    audit and never mutates audit history or collections. Exit codes: 0
    the report was generated, 2 the report could not be generated
    (unknown collection, unknown audit, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        report = (
            runtime
            .build_integrity_audit_report_service()
            .report_from_collection(collection, title=title)
        )

    except Exception as exc:
        _render_report_failure(
            exc, report_format=report_format, stderr=stderr
        )

        return 2

    _render_report(
        report,
        output_path=output_path,
        report_format=report_format,
        stdout=stdout,
    )

    return 0


def run_deployment_governance_audit_report_audits(
    *,
    title: str,
    audit_ids: list[str],
    output_path: str | None = None,
    report_format: str = "json",
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and generate a report from an explicit list of
    audit identifiers, preserving the requested order.

    This is a read-only inspection command: it never executes a new
    audit and never mutates audit history. Exit codes: 0 the report was
    generated, 2 the report could not be generated (unknown audit, or
    invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        report = (
            runtime
            .build_integrity_audit_report_service()
            .report_from_audits(title, audit_ids)
        )

    except Exception as exc:
        _render_report_failure(
            exc, report_format=report_format, stderr=stderr
        )

        return 2

    _render_report(
        report,
        output_path=output_path,
        report_format=report_format,
        stdout=stdout,
    )

    return 0


def _render_report(
    report: GovernanceIntegrityAuditReport,
    *,
    output_path: str | None,
    report_format: str,
    stdout: TextIO,
) -> None:
    content = (
        report.to_json()
        if report_format == "json"
        else report.to_markdown()
    )

    if output_path is not None:
        Path(output_path).write_text(content, encoding="utf-8")

        _render_report_generated_human(report, stdout=stdout)

        return

    stdout.write(content)

    if not content.endswith("\n"):
        stdout.write("\n")


def _render_report_generated_human(
    report: GovernanceIntegrityAuditReport,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Report generated\n\n")

    stdout.write(f"Title: {report.title}\n")

    stdout.write(f"Audits: {len(report.audits)}\n")

    health_rate = report.statistics.health_rate

    health_display = (
        "N/A"
        if health_rate is None
        else f"{health_rate * 100.0:.0f}%"
    )

    stdout.write(f"Health: {health_display}\n")


def _render_report_failure(
    error: Exception,
    *,
    report_format: str,
    stderr: TextIO,
) -> None:
    if report_format == "json":
        json.dump(
            {
                "status": "execution_failed",
                "error": str(error),
                "exit_code": 2,
            },
            stderr,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stderr.write("\n")

        return

    stderr.write(
        "Governance audit report could not be generated.\n"
    )

    stderr.write(f"Reason: {error}\n")
