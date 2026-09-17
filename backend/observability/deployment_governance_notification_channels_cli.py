from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
    GovernanceIntegrityNotificationChannelType,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_notification_channel_create(
    *,
    name: str,
    channel_type: str,
    destination: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new notification channel.

    Exit codes: 0 the channel was created, 2 the channel could not be
    created (duplicate name, invalid type, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel = (
            runtime
            .build_integrity_notification_channel_service()
            .create(
                name,
                GovernanceIntegrityNotificationChannelType(
                    channel_type
                ),
                destination,
            )
        )

    except Exception as exc:
        _render_channel_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_channel_json(channel, stdout=stdout)

    else:
        stdout.write("Channel created\n\n")

        _write_channel_fields(channel, stdout=stdout)

    return 0


def run_deployment_governance_notification_channel_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every notification channel.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channels = (
            runtime
            .build_integrity_notification_channel_service()
            .list()
        )

    except Exception as exc:
        _render_channel_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_channel_list_json(channels, stdout=stdout)

    else:
        stdout.write("Notification Channels\n")

        stdout.write("======================\n\n")

        if not channels:
            stdout.write(
                "No governance audit notification channels are "
                "configured.\n"
            )

        else:
            for channel in channels:
                stdout.write(f"{channel.name}\n")

    return 0


def run_deployment_governance_notification_channel_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one notification channel.

    Exit codes: 0 the channel was found, 2 the channel could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel = (
            runtime
            .build_integrity_notification_channel_service()
            .get(name)
        )

        if channel is None:
            raise KeyError(
                f"notification channel '{name}' was not found"
            )

    except Exception as exc:
        _render_channel_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_channel_json(channel, stdout=stdout)

    else:
        stdout.write("Channel\n\n")

        _write_channel_fields(channel, stdout=stdout)

    return 0


def run_deployment_governance_notification_channel_enable(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and enable one notification channel.

    Exit codes: 0 the channel was enabled, 2 the channel could not be
    enabled.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel = (
            runtime
            .build_integrity_notification_channel_service()
            .enable(name)
        )

    except Exception as exc:
        _render_channel_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_channel_json(channel, stdout=stdout)

    else:
        stdout.write("Channel enabled\n\n")

        _write_channel_fields(channel, stdout=stdout)

    return 0


def run_deployment_governance_notification_channel_disable(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and disable one notification channel.

    Exit codes: 0 the channel was disabled, 2 the channel could not
    be disabled.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel = (
            runtime
            .build_integrity_notification_channel_service()
            .disable(name)
        )

    except Exception as exc:
        _render_channel_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_channel_json(channel, stdout=stdout)

    else:
        stdout.write("Channel disabled\n\n")

        _write_channel_fields(channel, stdout=stdout)

    return 0


def run_deployment_governance_notification_channel_update(
    *,
    name: str,
    destination: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and update a notification channel's
    destination.

    Exit codes: 0 the channel was updated, 2 the channel could not be
    updated (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel = (
            runtime
            .build_integrity_notification_channel_service()
            .update_destination(name, destination)
        )

    except Exception as exc:
        _render_channel_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_channel_json(channel, stdout=stdout)

    else:
        stdout.write("Channel updated\n\n")

        _write_channel_fields(channel, stdout=stdout)

    return 0


def run_deployment_governance_notification_channel_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one notification channel.

    Exit codes: 0 the channel was deleted, 2 the channel could not be
    deleted (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_notification_channel_service().delete(
            name
        )

    except Exception as exc:
        _render_channel_failure(
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
        stdout.write(f"Channel '{name}' deleted.\n")

    return 0


def _write_channel_fields(
    channel: GovernanceIntegrityNotificationChannel,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Name: {channel.name}\n")

    stdout.write(f"Type: {channel.channel_type.value}\n")

    stdout.write(f"Destination: {channel.destination}\n")

    stdout.write(f"Enabled: {channel.enabled}\n")

    stdout.write(f"Created: {channel.created_at.isoformat()}\n")


def _render_channel_json(
    channel: GovernanceIntegrityNotificationChannel,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        channel.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_channel_list_json(
    channels: tuple[GovernanceIntegrityNotificationChannel, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [channel.to_dict() for channel in channels],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_channel_failure(
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
        "Governance audit notification channel operation could not "
        "be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
