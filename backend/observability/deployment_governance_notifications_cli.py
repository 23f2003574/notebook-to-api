from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertPolicy,
)
from .deployment_governance_notifications import (
    GovernanceIntegrityNotification,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)

DEFAULT_MINIMUM_SUCCESS_RATE = 0.0

DEFAULT_MAXIMUM_FAILURE_RATE = 100.0

DEFAULT_MAXIMUM_AVERAGE_DURATION_MS = 1_000_000_000.0


def run_deployment_governance_notifications_queue(
    *,
    minimum_success_rate: float = DEFAULT_MINIMUM_SUCCESS_RATE,
    maximum_failure_rate: float = DEFAULT_MAXIMUM_FAILURE_RATE,
    maximum_average_duration_ms: float = (
        DEFAULT_MAXIMUM_AVERAGE_DURATION_MS
    ),
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence, generate alerts, and queue one notification
    per newly seen alert.

    Exit codes: 0 the queue operation completed (even if nothing was
    queued), 2 the operation could not be completed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = GovernanceIntegrityAlertPolicy(
            minimum_success_rate=minimum_success_rate,
            maximum_failure_rate=maximum_failure_rate,
            maximum_average_duration_ms=maximum_average_duration_ms,
        )

        notifications = (
            runtime
            .build_integrity_notification_service()
            .queue(policy)
        )

    except Exception as exc:
        _render_notifications_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_notification_list_json(notifications, stdout=stdout)

    else:
        stdout.write(f"Queued {len(notifications)} notification(s)\n\n")

        for notification in notifications:
            stdout.write(
                f"{notification.notification_id}: "
                f"[{notification.severity.value.upper()}] "
                f"{notification.message}\n"
            )

    return 0


def run_deployment_governance_notifications_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every queued notification.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        notifications = (
            runtime
            .build_integrity_notification_service()
            .list()
        )

    except Exception as exc:
        _render_notifications_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_notification_list_json(notifications, stdout=stdout)

    else:
        stdout.write("Notifications\n")

        stdout.write("=============\n\n")

        if not notifications:
            stdout.write(
                "No governance audit notifications are queued.\n"
            )

        else:
            for notification in notifications:
                stdout.write(f"{notification.notification_id}\n")

    return 0


def run_deployment_governance_notifications_show(
    *,
    notification_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one queued notification.

    Exit codes: 0 the notification was found, 2 the notification
    could not be found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        notification = (
            runtime
            .build_integrity_notification_service()
            .get(notification_id)
        )

        if notification is None:
            raise KeyError(
                f"notification '{notification_id}' was not found"
            )

    except Exception as exc:
        _render_notifications_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_notification_json(notification, stdout=stdout)

    else:
        stdout.write("Notification\n\n")

        _write_notification_fields(notification, stdout=stdout)

    return 0


def run_deployment_governance_notifications_delete(
    *,
    notification_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove one queued notification.

    Exit codes: 0 the notification was removed, 2 the notification
    could not be removed (unknown id, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_notification_service().delete(
            notification_id
        )

    except Exception as exc:
        _render_notifications_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "status": "deleted",
                "notification_id": notification_id,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(f"Notification '{notification_id}' deleted.\n")

    return 0


def run_deployment_governance_notifications_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every queued notification.

    Exit codes: 0 the notifications were cleared, 2 they could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_notification_service().clear()

    except Exception as exc:
        _render_notifications_failure(
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
        stdout.write("Notifications cleared.\n")

    return 0


def _write_notification_fields(
    notification: GovernanceIntegrityNotification,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(
        f"Notification ID: {notification.notification_id}\n"
    )

    stdout.write(f"Alert ID: {notification.alert_id}\n")

    stdout.write(f"Severity: {notification.severity.value}\n")

    stdout.write(f"Message: {notification.message}\n")

    stdout.write(f"Status: {notification.status.value}\n")

    stdout.write(f"Created: {notification.created_at.isoformat()}\n")


def _render_notification_json(
    notification: GovernanceIntegrityNotification,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        notification.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_notification_list_json(
    notifications: tuple[GovernanceIntegrityNotification, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [
            notification.to_dict()
            for notification in notifications
        ],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_notifications_failure(
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
        "Governance audit notification operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
