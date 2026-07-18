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
from .deployment_governance_provider_capabilities import (
    GovernanceIntegrityProviderCapabilities,
    validate_delivery_policy_capabilities,
)
from .deployment_governance_provider_health import (
    GovernanceIntegrityProviderHealth,
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


def run_deployment_governance_provider_capabilities(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the capabilities of the provider
    registered for one channel type.

    Exit codes: 0 the capabilities were found, 2 they could not be
    found or shown (unknown channel type, or no provider registered).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        capabilities = (
            runtime
            .build_integrity_provider_registry()
            .capabilities(resolved_channel_type)
        )

    except Exception as exc:
        _render_provider_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "channel_type": resolved_channel_type.value,
                **capabilities.to_dict(),
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Provider Capabilities\n\n")

        stdout.write(f"Channel type: {resolved_channel_type.value}\n")

        _write_capabilities_fields(capabilities, stdout=stdout)

    return 0


def run_deployment_governance_provider_validate(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and validate every configured delivery
    policy for channels of one channel type against that type's
    registered provider capabilities.

    Exit codes: 0 every checked policy is compatible (including when
    none are configured), 2 the validation could not be completed or
    a configured policy is incompatible with its provider's
    capabilities.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        capabilities = (
            runtime
            .build_integrity_provider_registry()
            .capabilities(resolved_channel_type)
        )

        channel_service = (
            runtime.build_integrity_notification_channel_service()
        )

        policy_service = (
            runtime.build_integrity_delivery_policy_service()
        )

        results: list[dict[str, object]] = []

        for channel in channel_service.list():
            if channel.channel_type is not resolved_channel_type:
                continue

            try:
                policy = policy_service.resolve(channel.name)

            except LookupError:
                continue

            try:
                validate_delivery_policy_capabilities(
                    policy, capabilities
                )

            except ValueError as exc:
                results.append(
                    {
                        "channel_name": channel.name,
                        "compatible": False,
                        "error": str(exc),
                    }
                )

            else:
                results.append(
                    {
                        "channel_name": channel.name,
                        "compatible": True,
                        "error": None,
                    }
                )

    except Exception as exc:
        _render_provider_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    incompatible = [
        result for result in results if not result["compatible"]
    ]

    if json_output:
        json.dump(
            {
                "channel_type": resolved_channel_type.value,
                "results": results,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Provider Policy Validation\n\n")

        stdout.write(f"Channel type: {resolved_channel_type.value}\n\n")

        if not results:
            stdout.write(
                "No delivery policies are configured for channels of "
                "this type.\n"
            )

        else:
            for result in results:
                status = (
                    "compatible"
                    if result["compatible"]
                    else "incompatible"
                )

                stdout.write(
                    f"{result['channel_name']}: {status}\n"
                )

                if result["error"] is not None:
                    stdout.write(f"  {result['error']}\n")

    return 2 if incompatible else 0


def run_deployment_governance_provider_health(
    *,
    channel_type: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and check the health of the provider
    registered for one channel type.

    Exit codes: 0 the health check was performed (whether healthy or
    unhealthy), 2 the health check could not be performed (unknown
    channel type, or no provider registered).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        resolved_channel_type = (
            GovernanceIntegrityNotificationChannelType(channel_type)
        )

        health = (
            runtime
            .build_integrity_provider_health_service()
            .check(resolved_channel_type)
        )

    except Exception as exc:
        _render_provider_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_health_json(health, stdout=stdout)

    else:
        stdout.write("Provider Health\n\n")

        _write_health_fields(health, stdout=stdout)

    return 0


def run_deployment_governance_provider_health_all(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and check the health of every registered
    delivery provider.

    Exit codes: 0 the health check was performed (whether every
    provider is healthy or not), 2 the health check could not be
    performed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        health_records = (
            runtime
            .build_integrity_provider_health_service()
            .check_all()
        )

    except Exception as exc:
        _render_provider_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [health.to_dict() for health in health_records],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Provider Health\n")

        stdout.write("===============\n\n")

        if not health_records:
            stdout.write(
                "No governance audit delivery providers are "
                "registered.\n"
            )

        else:
            for health in health_records:
                stdout.write(
                    f"{health.channel_type.value}: {health.status.value}\n"
                )

    return 0


def _write_health_fields(
    health: GovernanceIntegrityProviderHealth,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Channel type: {health.channel_type.value}\n")

    stdout.write(f"Status: {health.status.value}\n")

    stdout.write(f"Checked at: {health.checked_at.isoformat()}\n")

    if health.message is not None:
        stdout.write(f"Message: {health.message}\n")


def _render_health_json(
    health: GovernanceIntegrityProviderHealth,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        health.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _write_capabilities_fields(
    capabilities: GovernanceIntegrityProviderCapabilities,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Supports retry: {capabilities.supports_retry}\n")

    stdout.write(f"Supports timeout: {capabilities.supports_timeout}\n")

    stdout.write(
        f"Supports rate limit: {capabilities.supports_rate_limit}\n"
    )

    stdout.write(
        f"Supports attachments: {capabilities.supports_attachments}\n"
    )

    stdout.write(
        f"Supports markdown: {capabilities.supports_markdown}\n"
    )


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
