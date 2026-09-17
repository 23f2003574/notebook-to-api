from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_provider_authentication import (
    GovernanceIntegrityAuthenticationContext,
)

_REDACTED = "***REDACTED***"


def run_deployment_governance_provider_auth_show(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the (redacted) authentication
    context for one channel type.

    Secret values are never printed: header and parameter values are
    always redacted, whatever the output format.

    Exit codes: 0 the authentication context was built, 2 it could
    not be built (unknown channel type, no provider registered, or a
    required secret is missing).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        context = (
            runtime
            .build_integrity_provider_authentication_service()
            .build(resolved_channel_type)
        )

    except Exception as exc:
        _render_authentication_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            _redacted_context_dict(
                resolved_channel_type, context
            ),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Provider Authentication\n\n")

        stdout.write(f"Channel type: {resolved_channel_type.value}\n")

        stdout.write(
            f"Authentication type: {context.authentication_type.value}\n"
        )

        stdout.write(
            f"Header names: {sorted(context.headers.keys())}\n"
        )

        stdout.write(
            f"Parameter names: {sorted(context.parameters.keys())}\n"
        )

    return 0


def run_deployment_governance_provider_auth_validate(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and validate that an authentication context
    can be built for one channel type, without exposing secret
    values.

    Exit codes: 0 the authentication context could be built, 2 it
    could not be built (unknown channel type, no provider registered,
    or a required secret is missing).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        context = (
            runtime
            .build_integrity_provider_authentication_service()
            .build(resolved_channel_type)
        )

    except Exception as exc:
        _render_authentication_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "channel_type": resolved_channel_type.value,
                "authentication_type": (
                    context.authentication_type.value
                ),
                "valid": True,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            "Authentication context for channel type "
            f"'{resolved_channel_type.value}' can be built "
            "successfully "
            f"({context.authentication_type.value}).\n"
        )

    return 0


def _redacted_context_dict(
    channel_type: GovernanceIntegrityNotificationChannelType,
    context: GovernanceIntegrityAuthenticationContext,
) -> dict[str, object]:
    return {
        "channel_type": channel_type.value,
        "authentication_type": context.authentication_type.value,
        "headers": {key: _REDACTED for key in context.headers},
        "parameters": {key: _REDACTED for key in context.parameters},
    }


def _render_authentication_failure(
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
        "Governance audit provider authentication operation could "
        "not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
