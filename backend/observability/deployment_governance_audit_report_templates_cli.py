from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportSource,
    GovernanceIntegrityAuditReportTemplate,
)
from .deployment_governance_audit_reports_cli import (
    _render_report,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_report_template_create(
    *,
    name: str,
    title: str,
    source: GovernanceIntegrityAuditReportSource,
    source_name: str,
    output_format: str = "json",
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a reusable report template.

    Exit codes: 0 the template was created, 2 the template could not be
    created (duplicate name, unknown source, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        template = (
            runtime
            .build_integrity_audit_report_template_service()
            .create(name, title, source, source_name, output_format)
        )

    except Exception as exc:
        _render_template_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_template_json(template, stdout=stdout)

    else:
        stdout.write("Template created\n\n")

        stdout.write(f"Name: {template.name}\n")

    return 0


def run_deployment_governance_audit_report_template_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every report template.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        templates = (
            runtime
            .build_integrity_audit_report_template_service()
            .list()
        )

    except Exception as exc:
        _render_template_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [template.to_dict() for template in templates],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Report Templates\n")

        stdout.write("================\n\n")

        if not templates:
            stdout.write(
                "No governance audit report templates have been "
                "created.\n"
            )

        else:
            for template in templates:
                stdout.write(f"{template.name}\n")

    return 0


def run_deployment_governance_audit_report_template_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one report template.

    Exit codes: 0 the template was found, 2 the template could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        template = (
            runtime
            .build_integrity_audit_report_template_service()
            .get(name)
        )

        if template is None:
            raise KeyError(
                f"report template '{name}' was not found"
            )

    except Exception as exc:
        _render_template_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_template_json(template, stdout=stdout)

    else:
        stdout.write("Report Template\n\n")

        stdout.write(f"Name: {template.name}\n")

        stdout.write(f"Title: {template.title}\n")

        stdout.write(f"Source: {template.source.value}\n")

        stdout.write(f"Source name: {template.source_name}\n")

        stdout.write(f"Output format: {template.output_format}\n")

    return 0


def run_deployment_governance_audit_report_template_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one report template.

    Exit codes: 0 the template was deleted, 2 the template could not be
    deleted (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_report_template_service().delete(
            name
        )

    except Exception as exc:
        _render_template_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "deleted", "name": name},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(f"Report template '{name}' deleted.\n")

    return 0


def run_deployment_governance_audit_report_template_generate(
    *,
    name: str,
    output_path: str | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence, resolve a template's source, and generate a
    fresh report. Rendering is delegated to the same logic used by
    `governance audits report`, using the template's own output_format.

    Exit codes: 0 the report was generated, 2 the report could not be
    generated (unknown template, unknown source, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_audit_report_template_service()

        template = service.get(name)

        if template is None:
            raise KeyError(
                f"report template '{name}' was not found"
            )

        report = service.generate(name)

    except Exception as exc:
        _render_template_failure(exc, json_output=False, stderr=stderr)

        return 2

    report_format = "json" if template.output_format == "json" else "md"

    _render_report(
        report,
        output_path=output_path,
        report_format=report_format,
        stdout=stdout,
    )

    return 0


def _render_template_json(
    template: GovernanceIntegrityAuditReportTemplate,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        template.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_template_failure(
    error: Exception,
    *,
    json_output: bool,
    stderr: TextIO,
) -> None:
    if json_output:
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
        "Governance audit report template operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
