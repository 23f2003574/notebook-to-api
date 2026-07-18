from __future__ import annotations

import json
import sys
from typing import Sequence, TextIO

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfiguration,
)


def parse_governance_provider_configuration_values(
    entries: Sequence[str] | None,
) -> dict[str, str]:
    """
    Parse repeated --set key=value options into a values mapping.

    Raises ValueError if an entry is not in key=value form or its key
    is empty.
    """

    values: dict[str, str] = {}

    for entry in entries or ():
        key, separator, value = entry.partition("=")

        if not separator:
            raise ValueError(
                f"--set option '{entry}' must be in key=value form"
            )

        normalized_key = key.strip()

        if not normalized_key:
            raise ValueError(
                f"--set option '{entry}' must have a non-empty key"
            )

        values[normalized_key] = value

    return values


def run_deployment_governance_provider_config_create(
    *,
    channel_type: str,
    values: Sequence[str] | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new provider configuration.

    Exit codes: 0 the configuration was created, 2 it could not be
    created (unknown channel type, no provider registered, a
    configuration already exists, or invalid --set options).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        parsed_values = (
            parse_governance_provider_configuration_values(values)
        )

        configuration = (
            runtime
            .build_integrity_provider_configuration_service()
            .create(resolved_channel_type, parsed_values)
        )

    except Exception as exc:
        _render_configuration_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_configuration_json(configuration, stdout=stdout)

    else:
        stdout.write("Provider configuration created\n\n")

        _write_configuration_fields(configuration, stdout=stdout)

    return 0


def run_deployment_governance_provider_config_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every stored provider
    configuration.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        configurations = (
            runtime
            .build_integrity_provider_configuration_service()
            .list()
        )

    except Exception as exc:
        _render_configuration_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [
                configuration.to_dict()
                for configuration in configurations
            ],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Audit Provider Configurations\n")

        stdout.write("=========================================\n\n")

        if not configurations:
            stdout.write(
                "No governance audit provider configurations are "
                "stored.\n"
            )

        else:
            for configuration in configurations:
                stdout.write(
                    f"{configuration.channel_type.value}: "
                    f"{dict(configuration.values)}\n"
                )

    return 0


def run_deployment_governance_provider_config_show(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one stored provider configuration.

    Exit codes: 0 the configuration was found, 2 it could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        configuration = (
            runtime
            .build_integrity_provider_configuration_service()
            .get(resolved_channel_type)
        )

        if configuration is None:
            raise LookupError(
                "no provider configuration is stored for channel "
                f"type '{resolved_channel_type.value}'"
            )

    except Exception as exc:
        _render_configuration_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_configuration_json(configuration, stdout=stdout)

    else:
        stdout.write("Provider Configuration\n\n")

        _write_configuration_fields(configuration, stdout=stdout)

    return 0


def run_deployment_governance_provider_config_update(
    *,
    channel_type: str,
    values: Sequence[str] | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and replace an existing provider
    configuration's complete set of values.

    Exit codes: 0 the configuration was updated, 2 it could not be
    updated (unknown channel type, no configuration stored, or
    invalid --set options).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        parsed_values = (
            parse_governance_provider_configuration_values(values)
        )

        configuration = (
            runtime
            .build_integrity_provider_configuration_service()
            .update(resolved_channel_type, parsed_values)
        )

    except Exception as exc:
        _render_configuration_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_configuration_json(configuration, stdout=stdout)

    else:
        stdout.write("Provider configuration updated\n\n")

        _write_configuration_fields(configuration, stdout=stdout)

    return 0


def run_deployment_governance_provider_config_delete(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one stored provider
    configuration.

    Exit codes: 0 the configuration was deleted, 2 it could not be
    deleted (unknown channel type, or no configuration stored).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        runtime.build_integrity_provider_configuration_service().delete(
            resolved_channel_type
        )

    except Exception as exc:
        _render_configuration_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "deleted", "channel_type": channel_type},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Provider configuration for '{channel_type}' deleted.\n"
        )

    return 0


def _write_configuration_fields(
    configuration: GovernanceIntegrityProviderConfiguration,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Channel type: {configuration.channel_type.value}\n")

    stdout.write(f"Values: {dict(configuration.values)}\n")

    stdout.write(f"Updated at: {configuration.updated_at.isoformat()}\n")


def _render_configuration_json(
    configuration: GovernanceIntegrityProviderConfiguration,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        configuration.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_configuration_failure(
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
        "Governance audit provider configuration operation could not "
        "be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
