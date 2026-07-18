from __future__ import annotations

import json
import sys
from typing import TextIO
from uuid import UUID

from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_delivery_scheduler import (
    GovernanceIntegrityScheduledDispatch,
)


def run_deployment_governance_scheduler_pending(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every pending scheduled dispatch.

    Exit codes: 0 the list was produced (even if empty), 2 it could
    not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        scheduled_dispatches = (
            runtime.build_integrity_delivery_scheduler()
            .pending_dispatches()
        )

    except Exception as exc:
        _render_scheduler_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    return _render_list(
        scheduled_dispatches, json_output=json_output, stdout=stdout
    )


def run_deployment_governance_scheduler_ready(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every scheduled dispatch ready to
    run right now.

    Exit codes: 0 the list was produced (even if empty), 2 it could
    not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        scheduled_dispatches = (
            runtime.build_integrity_delivery_scheduler().ready_dispatches()
        )

    except Exception as exc:
        _render_scheduler_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    return _render_list(
        scheduled_dispatches, json_output=json_output, stdout=stdout
    )


def run_deployment_governance_scheduler_show(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one scheduled dispatch.

    Exit codes: 0 the schedule was found, 2 it could not be found or
    shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        scheduled_dispatch = (
            runtime.build_integrity_delivery_scheduler().get(
                UUID(dispatch_id)
            )
        )

        if scheduled_dispatch is None:
            raise LookupError(
                f"no schedule found for dispatch '{dispatch_id}'"
            )

    except Exception as exc:
        _render_scheduler_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            scheduled_dispatch.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Scheduled Dispatch\n\n")

        _write_scheduled_dispatch_fields(scheduled_dispatch, stdout=stdout)

    return 0


def run_deployment_governance_scheduler_cancel(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and cancel one scheduled dispatch.

    Exit codes: 0 the schedule was cancelled, 2 it could not be
    cancelled (unknown dispatch ID).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        scheduled_dispatch = (
            runtime.build_integrity_delivery_scheduler().cancel(
                UUID(dispatch_id)
            )
        )

    except Exception as exc:
        _render_scheduler_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            scheduled_dispatch.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Scheduled dispatch cancelled\n\n")

        _write_scheduled_dispatch_fields(scheduled_dispatch, stdout=stdout)

    return 0


def _render_list(
    scheduled_dispatches: tuple[GovernanceIntegrityScheduledDispatch, ...],
    *,
    json_output: bool,
    stdout: TextIO,
) -> int:
    if json_output:
        json.dump(
            [
                scheduled_dispatch.to_dict()
                for scheduled_dispatch in scheduled_dispatches
            ],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Scheduled Dispatches\n")

        stdout.write("=====================\n\n")

        if not scheduled_dispatches:
            stdout.write("No scheduled dispatches found.\n")

        else:
            for scheduled_dispatch in scheduled_dispatches:
                stdout.write(
                    f"{scheduled_dispatch.dispatch_id}: "
                    f"{scheduled_dispatch.state.value} "
                    f"(attempt {scheduled_dispatch.attempt})\n"
                )

    return 0


def _write_scheduled_dispatch_fields(
    scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Dispatch ID: {scheduled_dispatch.dispatch_id}\n")

    stdout.write(f"State: {scheduled_dispatch.state.value}\n")

    stdout.write(
        f"Scheduled at: {scheduled_dispatch.scheduled_at.isoformat()}\n"
    )

    stdout.write(f"Attempt: {scheduled_dispatch.attempt}\n")


def _render_scheduler_failure(
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
        "Governance audit delivery scheduler operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
