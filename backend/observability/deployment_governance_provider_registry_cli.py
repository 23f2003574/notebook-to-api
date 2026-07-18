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
from .deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistration,
)


def run_deployment_governance_provider_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every registered governance audit
    delivery provider.

    Exit codes: 0 the list was produced, 2 the list could not be
    produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        registrations = (
            runtime.build_integrity_provider_registry().list()
        )

    except Exception as exc:
        _render_provider_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_registration_list_json(registrations, stdout=stdout)

    else:
        stdout.write("Governance Audit Delivery Providers\n")

        stdout.write("====================================\n\n")

        if not registrations:
            stdout.write(
                "No governance audit delivery providers are "
                "registered.\n"
            )

        else:
            for registration in registrations:
                stdout.write(
                    f"{registration.channel_type.value}: "
                    f"{registration.provider_name}\n"
                )

    return 0


def run_deployment_governance_provider_show(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the provider registered for one
    channel type.

    Exit codes: 0 the provider was found, 2 the provider could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        registrations_by_channel_type = {
            registration.channel_type: registration
            for registration in (
                runtime.build_integrity_provider_registry().list()
            )
        }

        registration = registrations_by_channel_type.get(
            resolved_channel_type
        )

        if registration is None:
            raise LookupError(
                "no delivery provider registered for channel type "
                f"'{resolved_channel_type.value}'"
            )

    except Exception as exc:
        _render_provider_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_registration_json(registration, stdout=stdout)

    else:
        stdout.write("Provider\n\n")

        _write_registration_fields(registration, stdout=stdout)

    return 0


def _write_registration_fields(
    registration: GovernanceIntegrityProviderRegistration,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Channel type: {registration.channel_type.value}\n")

    stdout.write(f"Provider: {registration.provider_name}\n")


def _render_registration_json(
    registration: GovernanceIntegrityProviderRegistration,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        registration.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_registration_list_json(
    registrations: tuple[GovernanceIntegrityProviderRegistration, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [registration.to_dict() for registration in registrations],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_provider_failure(
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
        "Governance audit delivery provider operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
