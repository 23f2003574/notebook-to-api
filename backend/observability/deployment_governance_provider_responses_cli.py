from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_provider_responses import (
    GovernanceIntegrityProviderResponse,
    GovernanceIntegrityProviderResponseOutcome,
)


def _run_dispatch_pipeline(runtime, dispatch_id: str):
    dispatch = runtime.notification_dispatch_repository.get(dispatch_id)

    if dispatch is None:
        raise KeyError(
            f"notification dispatch '{dispatch_id}' was not found"
        )

    notification = runtime.notification_repository.get(
        dispatch.notification_id
    )

    if notification is None:
        raise LookupError(
            f"notification '{dispatch.notification_id}' was not found"
        )

    channel = runtime.notification_channel_repository.get(
        dispatch.channel_name
    )

    if channel is None:
        raise LookupError(
            f"notification channel '{dispatch.channel_name}' was not "
            "found"
        )

    provider = runtime.build_integrity_provider_registry().resolve(
        channel.channel_type
    )

    request = runtime.build_integrity_provider_request_service().build(
        notification, channel
    )

    response = provider.deliver(request)

    outcome = runtime.build_integrity_provider_response_service().process(
        response
    )

    return response, outcome


def run_deployment_governance_provider_response_show(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence, deliver the request built for one queued
    dispatch, and show the raw provider response alongside the
    normalized delivery outcome.

    This does not persist anything: it is a read-only inspection of
    what delivering this dispatch right now would produce.

    Exit codes: 0 the response was produced and normalized, 2 it
    could not be (unknown dispatch, missing notification/channel/
    provider, or an unsupported response status code).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        response, outcome = _run_dispatch_pipeline(runtime, dispatch_id)

    except Exception as exc:
        _render_response_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "dispatch_id": dispatch_id,
                "response": response.to_dict(),
                "outcome": outcome.to_dict(),
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Provider Response\n\n")

        _write_response_fields(response, stdout=stdout)

        stdout.write("\nNormalized Outcome\n\n")

        _write_outcome_fields(outcome, stdout=stdout)

    return 0


def run_deployment_governance_provider_response_validate(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and validate that a response can be
    delivered and normalized for one queued dispatch.

    Exit codes: 0 the response was produced and normalized (whether
    the normalized outcome itself is a success or a failure), 2 it
    could not be produced or normalized at all.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        _response, outcome = _run_dispatch_pipeline(runtime, dispatch_id)

    except Exception as exc:
        _render_response_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "dispatch_id": dispatch_id,
                "valid": True,
                "success": outcome.success,
                "retryable": outcome.retryable,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Response for dispatch '{dispatch_id}' was normalized "
            f"successfully (success={outcome.success}, "
            f"retryable={outcome.retryable}).\n"
        )

    return 0


def _write_response_fields(
    response: GovernanceIntegrityProviderResponse,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Status code: {response.status_code}\n")

    stdout.write(f"Duration ms: {response.duration_ms}\n")

    stdout.write(f"Body: {dict(response.body)}\n")


def _write_outcome_fields(
    outcome: GovernanceIntegrityProviderResponseOutcome,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Success: {outcome.success}\n")

    stdout.write(f"Provider status: {outcome.provider_status}\n")

    stdout.write(f"Retryable: {outcome.retryable}\n")

    if outcome.message is not None:
        stdout.write(f"Message: {outcome.message}\n")


def _render_response_failure(
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
        "Governance audit provider response operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
