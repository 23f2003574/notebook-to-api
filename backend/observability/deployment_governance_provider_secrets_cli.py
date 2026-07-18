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
from .deployment_governance_provider_configuration_cli import (
    parse_governance_provider_configuration_values,
)
from .deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecrets,
)


def run_deployment_governance_provider_secrets_create(
    *,
    channel_type: str,
    values: Sequence[str] | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new provider secret set.

    Exit codes: 0 the secret set was created, 2 it could not be
    created (unknown channel type, no provider registered, a secret
    set already exists, or invalid --set options).
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

        secrets = (
            runtime
            .build_integrity_provider_secrets_service()
            .create(resolved_channel_type, parsed_values)
        )

    except Exception as exc:
        _render_secrets_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_secrets_json(secrets, stdout=stdout)

    else:
        stdout.write("Provider secrets created\n\n")

        _write_secrets_fields(secrets, stdout=stdout)

    return 0


def run_deployment_governance_provider_secrets_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every stored provider secret set.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        secrets_list = (
            runtime.build_integrity_provider_secrets_service().list()
        )

    except Exception as exc:
        _render_secrets_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [secrets.to_dict() for secrets in secrets_list],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Audit Provider Secrets\n")

        stdout.write("==================================\n\n")

        if not secrets_list:
            stdout.write(
                "No governance audit provider secrets are stored.\n"
            )

        else:
            for secrets in secrets_list:
                stdout.write(
                    f"{secrets.channel_type.value}: "
                    f"{sorted(secrets.values.keys())}\n"
                )

    return 0


def run_deployment_governance_provider_secrets_show(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one stored provider secret set.

    Exit codes: 0 the secret set was found, 2 it could not be found or
    shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        secrets = (
            runtime
            .build_integrity_provider_secrets_service()
            .get(resolved_channel_type)
        )

        if secrets is None:
            raise LookupError(
                "no provider secrets are stored for channel type "
                f"'{resolved_channel_type.value}'"
            )

    except Exception as exc:
        _render_secrets_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_secrets_json(secrets, stdout=stdout)

    else:
        stdout.write("Provider Secrets\n\n")

        _write_secrets_fields(secrets, stdout=stdout)

    return 0


def run_deployment_governance_provider_secrets_update(
    *,
    channel_type: str,
    values: Sequence[str] | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and replace an existing provider secret
    set's complete set of values.

    Exit codes: 0 the secret set was updated, 2 it could not be
    updated (unknown channel type, no secret set stored, or invalid
    --set options).
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

        secrets = (
            runtime
            .build_integrity_provider_secrets_service()
            .update(resolved_channel_type, parsed_values)
        )

    except Exception as exc:
        _render_secrets_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_secrets_json(secrets, stdout=stdout)

    else:
        stdout.write("Provider secrets updated\n\n")

        _write_secrets_fields(secrets, stdout=stdout)

    return 0


def run_deployment_governance_provider_secrets_delete(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one stored provider secret set.

    Exit codes: 0 the secret set was deleted, 2 it could not be
    deleted (unknown channel type, or no secret set stored).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        runtime.build_integrity_provider_secrets_service().delete(
            resolved_channel_type
        )

    except Exception as exc:
        _render_secrets_failure(
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
            f"Provider secrets for '{channel_type}' deleted.\n"
        )

    return 0


def _write_secrets_fields(
    secrets: GovernanceIntegrityProviderSecrets,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Channel type: {secrets.channel_type.value}\n")

    stdout.write(f"Keys: {sorted(secrets.values.keys())}\n")

    stdout.write(f"Updated at: {secrets.updated_at.isoformat()}\n")


def _render_secrets_json(
    secrets: GovernanceIntegrityProviderSecrets,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        secrets.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_secrets_failure(
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
        "Governance audit provider secrets operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
