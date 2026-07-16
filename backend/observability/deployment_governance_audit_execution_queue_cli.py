from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_execution_queue import (
    GovernanceIntegrityAuditExecutionJob,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_queue_enqueue(
    *,
    schedule_name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and queue one schedule as a pending job.

    Exit codes: 0 the job was queued, 2 the job could not be queued
    (unknown schedule, disabled schedule, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        job = (
            runtime
            .build_integrity_audit_execution_queue_service()
            .enqueue_schedule(schedule_name)
        )

    except Exception as exc:
        _render_queue_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_job_json(job, stdout=stdout)

    else:
        _render_job_queued_human(job, stdout=stdout)

    return 0


def run_deployment_governance_audit_queue_enqueue_due(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and queue every currently enabled schedule.

    Exit codes: 0 the queue operation completed (even if no schedules
    are enabled), 2 the operation could not be completed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        jobs = (
            runtime
            .build_integrity_audit_execution_queue_service()
            .enqueue_due()
        )

    except Exception as exc:
        _render_queue_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_job_list_json(jobs, stdout=stdout)

    else:
        stdout.write(f"Queued {len(jobs)} job(s)\n\n")

        for job in jobs:
            stdout.write(f"{job.job_id}\n")

    return 0


def run_deployment_governance_audit_queue_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every queued job.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        jobs = (
            runtime
            .build_integrity_audit_execution_queue_service()
            .list()
        )

    except Exception as exc:
        _render_queue_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_job_list_json(jobs, stdout=stdout)

    else:
        stdout.write("Execution Queue\n")

        stdout.write("===============\n\n")

        if not jobs:
            stdout.write(
                "No governance audit execution jobs are queued.\n"
            )

        else:
            for job in jobs:
                stdout.write(f"{job.job_id}\n")

    return 0


def run_deployment_governance_audit_queue_show(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one queued job.

    Exit codes: 0 the job was found, 2 the job could not be found or
    shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        job = (
            runtime
            .build_integrity_audit_execution_queue_service()
            .get(job_id)
        )

        if job is None:
            raise KeyError(
                f"execution job '{job_id}' was not found"
            )

    except Exception as exc:
        _render_queue_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_job_json(job, stdout=stdout)

    else:
        stdout.write("Job\n\n")

        _write_job_fields(job, stdout=stdout)

    return 0


def run_deployment_governance_audit_queue_delete(
    *,
    job_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove one job from the queue.

    Exit codes: 0 the job was removed, 2 the job could not be removed
    (unknown job, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_execution_queue_service().delete(
            job_id
        )

    except Exception as exc:
        _render_queue_failure(
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
        stdout.write(f"Job '{job_id}' deleted.\n")

    return 0


def run_deployment_governance_audit_queue_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every job from the queue.

    Exit codes: 0 the queue was cleared, 2 the queue could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_execution_queue_service().clear()

    except Exception as exc:
        _render_queue_failure(
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
        stdout.write("Execution queue cleared.\n")

    return 0


def _write_job_fields(
    job: GovernanceIntegrityAuditExecutionJob,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Job ID: {job.job_id}\n")

    stdout.write(f"Schedule: {job.schedule_name}\n")

    stdout.write(f"Template: {job.template_name}\n")

    stdout.write(f"Status: {job.status.value}\n")

    stdout.write(f"Queued: {job.queued_at.isoformat()}\n")


def _render_job_queued_human(
    job: GovernanceIntegrityAuditExecutionJob,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Job queued\n\n")

    stdout.write(f"Job ID: {job.job_id}\n")

    stdout.write(f"Schedule: {job.schedule_name}\n")

    stdout.write(f"Status: {job.status.value}\n")


def _render_job_json(
    job: GovernanceIntegrityAuditExecutionJob,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        job.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_job_list_json(
    jobs: tuple[GovernanceIntegrityAuditExecutionJob, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [job.to_dict() for job in jobs],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_queue_failure(
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
        "Governance audit execution queue operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
