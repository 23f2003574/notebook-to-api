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
from .deployment_governance_provider_requests import (
    GovernanceIntegrityProviderRequest,
)

_REDACTED = "***REDACTED***"


def _resolve_channel_and_notification(runtime, channel_type: str, notification_id: str):
    resolved_channel_type = GovernanceIntegrityNotificationChannelType(
        channel_type
    )

    channel = next(
        (
            candidate
            for candidate in (
                runtime
                .build_integrity_notification_channel_service()
                .list()
            )
            if candidate.channel_type is resolved_channel_type
        ),
        None,
    )

    if channel is None:
        raise LookupError(
            "no notification channel is registered for channel "
            f"type '{resolved_channel_type.value}'"
        )

    notification = runtime.notification_repository.get(notification_id)

    if notification is None:
        raise LookupError(
            f"notification '{notification_id}' was not found"
        )

    return channel, notification


def run_deployment_governance_provider_request_show(
    *,
    channel_type: str,
    notification_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the (redacted) provider request
    built for delivering one notification through one channel type.

    Header values are always redacted, since they may contain
    authentication credentials.

    Exit codes: 0 the request was built, 2 it could not be built
    (unknown channel type, no channel or notification found, no
    provider registered, or a required secret is missing).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel, notification = _resolve_channel_and_notification(
            runtime, channel_type, notification_id
        )

        request = (
            runtime
            .build_integrity_provider_request_service()
            .build(notification, channel)
        )

    except Exception as exc:
        _render_request_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            _redacted_request_dict(request),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Provider Request\n\n")

        stdout.write(f"Method: {request.method}\n")

        stdout.write(f"Endpoint: {request.endpoint}\n")

        stdout.write(f"Header names: {sorted(request.headers.keys())}\n")

        stdout.write(f"Body: {dict(request.body)}\n")

        stdout.write(f"Timeout seconds: {request.timeout_seconds}\n")

    return 0


def run_deployment_governance_provider_request_validate(
    *,
    channel_type: str,
    notification_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and validate that a provider request can be
    built for delivering one notification through one channel type,
    without exposing header values.

    Exit codes: 0 the request could be built, 2 it could not be built
    (unknown channel type, no channel or notification found, no
    provider registered, or a required secret is missing).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        channel, notification = _resolve_channel_and_notification(
            runtime, channel_type, notification_id
        )

        request = (
            runtime
            .build_integrity_provider_request_service()
            .build(notification, channel)
        )

    except Exception as exc:
        _render_request_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "channel_type": channel_type,
                "notification_id": notification_id,
                "method": request.method,
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
            f"Request for notification '{notification_id}' through "
            f"channel type '{channel_type}' can be built successfully "
            f"({request.method}).\n"
        )

    return 0


def _redacted_request_dict(
    request: GovernanceIntegrityProviderRequest,
) -> dict[str, object]:
    return {
        "method": request.method,
        "endpoint": request.endpoint,
        "headers": {key: _REDACTED for key in request.headers},
        "body": dict(request.body),
        "timeout_seconds": request.timeout_seconds,
    }


def _render_request_failure(
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
        "Governance audit provider request operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
