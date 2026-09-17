from __future__ import annotations

import json
import sys
from typing import Sequence, TextIO

from .deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertSeverity,
)
from .deployment_governance_notification_preferences import (
    GovernanceIntegrityNotificationPreference,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_notification_preference_create(
    *,
    name: str,
    minimum_severity: str,
    channels: Sequence[str],
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new notification preference.

    Exit codes: 0 the preference was created, 2 the preference could
    not be created (duplicate name, invalid severity, empty channel
    list, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        preference = (
            runtime
            .build_integrity_notification_preference_service()
            .create(
                name,
                GovernanceIntegrityAlertSeverity(minimum_severity),
                tuple(channels),
            )
        )

    except Exception as exc:
        _render_preference_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_preference_json(preference, stdout=stdout)

    else:
        stdout.write("Preference created\n\n")

        _write_preference_fields(preference, stdout=stdout)

    return 0


def run_deployment_governance_notification_preference_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every notification preference.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        preferences = (
            runtime
            .build_integrity_notification_preference_service()
            .list()
        )

    except Exception as exc:
        _render_preference_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_preference_list_json(preferences, stdout=stdout)

    else:
        stdout.write("Notification Preferences\n")

        stdout.write("=========================\n\n")

        if not preferences:
            stdout.write(
                "No governance audit notification preferences are "
                "configured.\n"
            )

        else:
            for preference in preferences:
                stdout.write(f"{preference.name}\n")

    return 0


def run_deployment_governance_notification_preference_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one notification preference.

    Exit codes: 0 the preference was found, 2 the preference could
    not be found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        preference = (
            runtime
            .build_integrity_notification_preference_service()
            .get(name)
        )

        if preference is None:
            raise KeyError(
                f"notification preference '{name}' was not found"
            )

    except Exception as exc:
        _render_preference_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_preference_json(preference, stdout=stdout)

    else:
        stdout.write("Preference\n\n")

        _write_preference_fields(preference, stdout=stdout)

    return 0


def run_deployment_governance_notification_preference_update(
    *,
    name: str,
    minimum_severity: str | None = None,
    channels: Sequence[str] | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and update an existing notification
    preference.

    Exit codes: 0 the preference was updated, 2 the preference could
    not be updated (unknown name, invalid severity, empty channel
    list, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        preference = (
            runtime
            .build_integrity_notification_preference_service()
            .update(
                name,
                minimum_severity=(
                    None
                    if minimum_severity is None
                    else GovernanceIntegrityAlertSeverity(
                        minimum_severity
                    )
                ),
                channels=(
                    None
                    if channels is None
                    else tuple(channels)
                ),
            )
        )

    except Exception as exc:
        _render_preference_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_preference_json(preference, stdout=stdout)

    else:
        stdout.write("Preference updated\n\n")

        _write_preference_fields(preference, stdout=stdout)

    return 0


def run_deployment_governance_notification_preference_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one notification preference.

    Exit codes: 0 the preference was deleted, 2 the preference could
    not be deleted (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_notification_preference_service().delete(
            name
        )

    except Exception as exc:
        _render_preference_failure(
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
        stdout.write(f"Preference '{name}' deleted.\n")

    return 0


def _write_preference_fields(
    preference: GovernanceIntegrityNotificationPreference,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Name: {preference.name}\n")

    stdout.write(
        f"Minimum severity: {preference.minimum_severity.value}\n"
    )

    stdout.write(
        f"Channels: {', '.join(preference.channels)}\n"
    )

    stdout.write(f"Enabled: {preference.enabled}\n")

    stdout.write(f"Created: {preference.created_at.isoformat()}\n")


def _render_preference_json(
    preference: GovernanceIntegrityNotificationPreference,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        preference.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_preference_list_json(
    preferences: tuple[GovernanceIntegrityNotificationPreference, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [preference.to_dict() for preference in preferences],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_preference_failure(
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
        "Governance audit notification preference operation could "
        "not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
