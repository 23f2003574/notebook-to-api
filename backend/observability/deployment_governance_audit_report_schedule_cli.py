from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_report_schedule import (
    GovernanceIntegrityAuditReportSchedule,
    GovernanceIntegrityReportScheduleFrequency,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_report_schedule_create(
    *,
    name: str,
    template_name: str,
    frequency: GovernanceIntegrityReportScheduleFrequency,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new report schedule.

    Exit codes: 0 the schedule was created, 2 the schedule could not be
    created (duplicate name, unknown template, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        schedule = (
            runtime
            .build_integrity_audit_report_schedule_service()
            .create(name, template_name, frequency)
        )

    except Exception as exc:
        _render_schedule_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_schedule_json(schedule, stdout=stdout)

    else:
        stdout.write("Schedule created\n\n")

        _write_schedule_fields(schedule, stdout=stdout)

    return 0


def run_deployment_governance_audit_report_schedule_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every report schedule.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        schedules = (
            runtime
            .build_integrity_audit_report_schedule_service()
            .list()
        )

    except Exception as exc:
        _render_schedule_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [schedule.to_dict() for schedule in schedules],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Schedules\n")

        stdout.write("=========\n\n")

        if not schedules:
            stdout.write(
                "No governance audit report schedules have been "
                "created.\n"
            )

        else:
            for schedule in schedules:
                stdout.write(f"{schedule.name}\n")

    return 0


def run_deployment_governance_audit_report_schedule_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one report schedule.

    Exit codes: 0 the schedule was found, 2 the schedule could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        schedule = (
            runtime
            .build_integrity_audit_report_schedule_service()
            .get(name)
        )

        if schedule is None:
            raise KeyError(
                f"report schedule '{name}' was not found"
            )

    except Exception as exc:
        _render_schedule_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_schedule_json(schedule, stdout=stdout)

    else:
        stdout.write("Schedule\n\n")

        _write_schedule_fields(schedule, stdout=stdout)

    return 0


def run_deployment_governance_audit_report_schedule_enable(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    return _run_set_enabled(
        name=name,
        enabled=True,
        json_output=json_output,
        stdout=stdout,
        stderr=stderr,
    )


def run_deployment_governance_audit_report_schedule_disable(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    return _run_set_enabled(
        name=name,
        enabled=False,
        json_output=json_output,
        stdout=stdout,
        stderr=stderr,
    )


def _run_set_enabled(
    *,
    name: str,
    enabled: bool,
    json_output: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """
    Bootstrap persistence and enable/disable one report schedule.

    Exit codes: 0 the schedule was updated, 2 the schedule could not be
    updated (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_audit_report_schedule_service()

        schedule = (
            service.enable(name) if enabled else service.disable(name)
        )

    except Exception as exc:
        _render_schedule_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_schedule_json(schedule, stdout=stdout)

    else:
        status = "enabled" if enabled else "disabled"

        stdout.write(f"Schedule '{name}' {status}.\n")

    return 0


def run_deployment_governance_audit_report_schedule_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one report schedule.

    Exit codes: 0 the schedule was deleted, 2 the schedule could not be
    deleted (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_report_schedule_service().delete(
            name
        )

    except Exception as exc:
        _render_schedule_failure(
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
        stdout.write(f"Schedule '{name}' deleted.\n")

    return 0


def _write_schedule_fields(
    schedule: GovernanceIntegrityAuditReportSchedule,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Name: {schedule.name}\n")

    stdout.write(f"Template: {schedule.template_name}\n")

    stdout.write(f"Frequency: {schedule.frequency.value}\n")

    stdout.write(
        f"Status: {'enabled' if schedule.enabled else 'disabled'}\n"
    )


def _render_schedule_json(
    schedule: GovernanceIntegrityAuditReportSchedule,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        schedule.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_schedule_failure(
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
        "Governance audit report schedule operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
