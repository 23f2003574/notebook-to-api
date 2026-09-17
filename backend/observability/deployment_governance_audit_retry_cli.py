from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_retry import (
    GovernanceIntegrityRetryRecord,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_retry_run(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and retry one failed execution job.

    Exit codes: 0 the retry was queued, 2 the retry could not be
    queued (unknown execution, non-failed execution, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = (
            runtime
            .build_integrity_audit_retry_service()
            .retry(job_id)
        )

    except Exception as exc:
        _render_retry_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        _render_record_queued_human(record, stdout=stdout)

    return 0


def run_deployment_governance_audit_retry_history(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every stored retry record.

    Exit codes: 0 the history was produced (even if empty), 2 the
    history could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = (
            runtime
            .build_integrity_audit_retry_service()
            .history()
        )

    except Exception as exc:
        _render_retry_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_list_json(records, stdout=stdout)

    else:
        stdout.write("Retry History\n")

        stdout.write("=============\n\n")

        if not records:
            stdout.write(
                "No governance audit retry records are stored.\n"
            )

        else:
            for record in records:
                stdout.write(
                    f"{record.original_job_id} -> {record.new_job_id}\n"
                )

    return 0


def run_deployment_governance_audit_retry_show(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one stored retry record.

    Exit codes: 0 the record was found, 2 the record could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = (
            runtime
            .build_integrity_audit_retry_service()
            .get(job_id)
        )

        if record is None:
            raise KeyError(
                f"retry record '{job_id}' was not found"
            )

    except Exception as exc:
        _render_retry_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        stdout.write("Retry Record\n\n")

        _write_record_fields(record, stdout=stdout)

    return 0


def run_deployment_governance_audit_retry_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every stored retry record.

    Exit codes: 0 the history was cleared, 2 the history could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_retry_service().clear()

    except Exception as exc:
        _render_retry_failure(
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
        stdout.write("Retry history cleared.\n")

    return 0


def _write_record_fields(
    record: GovernanceIntegrityRetryRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Retry ID: {record.retry_id}\n")

    stdout.write(f"Original job: {record.original_job_id}\n")

    stdout.write(f"New job: {record.new_job_id}\n")

    stdout.write(f"Created: {record.created_at.isoformat()}\n")


def _render_record_queued_human(
    record: GovernanceIntegrityRetryRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Retry queued\n\n")

    _write_record_fields(record, stdout=stdout)


def _render_record_json(
    record: GovernanceIntegrityRetryRecord,
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
    records: tuple[GovernanceIntegrityRetryRecord, ...],
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


def _render_retry_failure(
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
        "Governance audit retry operation could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
