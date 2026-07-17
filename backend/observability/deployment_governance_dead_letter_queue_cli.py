from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_dead_letter_queue import (
    GovernanceIntegrityDeadLetterRecord,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_dead_letter_archive(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and archive one failed execution into the
    dead letter queue.

    Exit codes: 0 the record was archived, 2 the record could not be
    archived (unknown execution, non-failed execution, duplicate
    archive, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = (
            runtime
            .build_integrity_dead_letter_service()
            .archive(job_id)
        )

    except Exception as exc:
        _render_dead_letter_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        _render_record_archived_human(record, stdout=stdout)

    return 0


def run_deployment_governance_dead_letter_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every dead letter record.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = (
            runtime
            .build_integrity_dead_letter_service()
            .list()
        )

    except Exception as exc:
        _render_dead_letter_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_list_json(records, stdout=stdout)

    else:
        stdout.write("Dead Letter Queue\n")

        stdout.write("=================\n\n")

        if not records:
            stdout.write(
                "No governance audit dead letter records are stored.\n"
            )

        else:
            for record in records:
                stdout.write(f"{record.job_id}\n")

    return 0


def run_deployment_governance_dead_letter_show(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one dead letter record.

    Exit codes: 0 the record was found, 2 the record could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = (
            runtime
            .build_integrity_dead_letter_service()
            .get(job_id)
        )

        if record is None:
            raise KeyError(
                f"dead letter record '{job_id}' was not found"
            )

    except Exception as exc:
        _render_dead_letter_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        stdout.write("Dead Letter Record\n\n")

        _write_record_fields(record, stdout=stdout)

    return 0


def run_deployment_governance_dead_letter_delete(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove one dead letter record.

    Exit codes: 0 the record was removed, 2 the record could not be
    removed (unknown record, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_dead_letter_service().delete(job_id)

    except Exception as exc:
        _render_dead_letter_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "deleted", "job_id": job_id},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(f"Dead letter record '{job_id}' deleted.\n")

    return 0


def run_deployment_governance_dead_letter_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every dead letter record.

    Exit codes: 0 the queue was cleared, 2 the queue could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_dead_letter_service().clear()

    except Exception as exc:
        _render_dead_letter_failure(
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
        stdout.write("Dead letter queue cleared.\n")

    return 0


def _write_record_fields(
    record: GovernanceIntegrityDeadLetterRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Dead Letter ID: {record.dead_letter_id}\n")

    stdout.write(f"Job ID: {record.job_id}\n")

    stdout.write(f"Template: {record.template_name}\n")

    stdout.write(f"Error: {record.error}\n")

    stdout.write(f"Failed: {record.failed_at.isoformat()}\n")


def _render_record_archived_human(
    record: GovernanceIntegrityDeadLetterRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Record archived\n\n")

    _write_record_fields(record, stdout=stdout)


def _render_record_json(
    record: GovernanceIntegrityDeadLetterRecord,
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
    records: tuple[GovernanceIntegrityDeadLetterRecord, ...],
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


def _render_dead_letter_failure(
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
        "Governance audit dead letter queue operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
