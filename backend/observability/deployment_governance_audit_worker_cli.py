from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_worker import (
    GovernanceIntegrityAuditExecutionRecord,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_worker_run(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and run one queued execution job.

    Exit codes: 0 the job was run (whether it succeeded or failed), 2
    the job could not be run (unknown job, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = runtime.build_integrity_audit_worker().run_job(job_id)

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        _render_record_human(record, stdout=stdout)

    return 0


def run_deployment_governance_audit_worker_run_all(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and run every currently queued job.

    Exit codes: 0 the run completed (even if no jobs were queued), 2
    the run could not be completed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = runtime.build_integrity_audit_worker().run_all()

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_list_json(records, stdout=stdout)

    else:
        stdout.write(f"Ran {len(records)} job(s)\n\n")

        for record in records:
            stdout.write(
                f"{record.job_id}: {record.result.value}\n"
            )

    return 0


def run_deployment_governance_audit_worker_history(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every stored execution record.

    Exit codes: 0 the history was produced (even if empty), 2 the
    history could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = runtime.build_integrity_audit_worker().history()

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_list_json(records, stdout=stdout)

    else:
        stdout.write("Execution History\n")

        stdout.write("=================\n\n")

        if not records:
            stdout.write(
                "No governance audit execution records are stored.\n"
            )

        else:
            for record in records:
                stdout.write(
                    f"{record.job_id}: {record.result.value}\n"
                )

    return 0


def run_deployment_governance_audit_worker_show(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one stored execution record.

    Exit codes: 0 the record was found, 2 the record could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = runtime.build_integrity_audit_worker().get(job_id)

        if record is None:
            raise KeyError(
                f"execution record '{job_id}' was not found"
            )

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        stdout.write("Execution Record\n\n")

        _write_record_fields(record, stdout=stdout)

    return 0


def run_deployment_governance_audit_worker_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every stored execution record.

    Exit codes: 0 the history was cleared, 2 the history could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_worker().clear_history()

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "cleared"},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Execution history cleared.\n")

    return 0


def _write_record_fields(
    record: GovernanceIntegrityAuditExecutionRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Job ID: {record.job_id}\n")

    stdout.write(f"Template: {record.template_name}\n")

    stdout.write(f"Result: {record.result.value}\n")

    if record.error is not None:
        stdout.write(f"Error: {record.error}\n")

    stdout.write(f"Started: {record.started_at.isoformat()}\n")

    stdout.write(f"Finished: {record.finished_at.isoformat()}\n")


def _render_record_human(
    record: GovernanceIntegrityAuditExecutionRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Job run\n\n")

    _write_record_fields(record, stdout=stdout)


def _render_record_json(
    record: GovernanceIntegrityAuditExecutionRecord,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        record.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_record_list_json(
    records: tuple[GovernanceIntegrityAuditExecutionRecord, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [record.to_dict() for record in records],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_worker_failure(
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
        "Governance audit execution worker operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
