from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_notification_dispatcher import (
    GovernanceIntegrityNotificationDispatch,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_notification_dispatch_run(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and dispatch every pending notification to
    every enabled channel.

    Exit codes: 0 the dispatch run completed (even if nothing new was
    dispatched), 2 the run could not be completed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        dispatches = (
            runtime
            .build_integrity_notification_dispatcher()
            .dispatch_pending()
        )

    except Exception as exc:
        _render_dispatch_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_dispatch_list_json(dispatches, stdout=stdout)

    else:
        stdout.write(f"Dispatched {len(dispatches)} record(s)\n\n")

        for dispatch in dispatches:
            stdout.write(
                f"{dispatch.dispatch_id}: "
                f"{dispatch.notification_id} -> {dispatch.channel_name}\n"
            )

    return 0


def run_deployment_governance_notification_dispatch_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every dispatch record.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        dispatches = (
            runtime
            .build_integrity_notification_dispatcher()
            .list()
        )

    except Exception as exc:
        _render_dispatch_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_dispatch_list_json(dispatches, stdout=stdout)

    else:
        stdout.write("Notification Dispatches\n")

        stdout.write("========================\n\n")

        if not dispatches:
            stdout.write(
                "No governance audit notification dispatches are "
                "recorded.\n"
            )

        else:
            for dispatch in dispatches:
                stdout.write(f"{dispatch.dispatch_id}\n")

    return 0


def run_deployment_governance_notification_dispatch_show(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one dispatch record.

    Exit codes: 0 the record was found, 2 the record could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        dispatch = (
            runtime
            .build_integrity_notification_dispatcher()
            .get(dispatch_id)
        )

        if dispatch is None:
            raise KeyError(
                f"notification dispatch '{dispatch_id}' was not found"
            )

    except Exception as exc:
        _render_dispatch_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_dispatch_json(dispatch, stdout=stdout)

    else:
        stdout.write("Dispatch\n\n")

        _write_dispatch_fields(dispatch, stdout=stdout)

    return 0


def run_deployment_governance_notification_dispatch_delete(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove one dispatch record.

    Exit codes: 0 the record was removed, 2 the record could not be
    removed (unknown id, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_notification_dispatcher().delete(
            dispatch_id
        )

    except Exception as exc:
        _render_dispatch_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "deleted", "dispatch_id": dispatch_id},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(f"Dispatch '{dispatch_id}' deleted.\n")

    return 0


def run_deployment_governance_notification_dispatch_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every dispatch record.

    Exit codes: 0 the records were cleared, 2 they could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_notification_dispatcher().clear()

    except Exception as exc:
        _render_dispatch_failure(
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
        stdout.write("Notification dispatches cleared.\n")

    return 0


def _write_dispatch_fields(
    dispatch: GovernanceIntegrityNotificationDispatch,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Dispatch ID: {dispatch.dispatch_id}\n")

    stdout.write(f"Notification ID: {dispatch.notification_id}\n")

    stdout.write(f"Channel: {dispatch.channel_name}\n")

    stdout.write(f"Status: {dispatch.status.value}\n")

    stdout.write(f"Created: {dispatch.created_at.isoformat()}\n")


def _render_dispatch_json(
    dispatch: GovernanceIntegrityNotificationDispatch,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        dispatch.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_dispatch_list_json(
    dispatches: tuple[GovernanceIntegrityNotificationDispatch, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [dispatch.to_dict() for dispatch in dispatches],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_dispatch_failure(
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
        "Governance audit notification dispatch operation could not "
        "be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
