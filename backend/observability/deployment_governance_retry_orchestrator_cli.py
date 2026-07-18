from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_retry_orchestrator import (
    GovernanceIntegrityRetryDecision,
)


def _evaluate_dispatch(runtime, dispatch_id: str, attempt: int):
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

    policy = runtime.build_integrity_delivery_policy_service().resolve(
        channel.name
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

    decision = runtime.build_integrity_retry_orchestrator().evaluate(
        outcome, policy, attempt
    )

    return decision


def run_deployment_governance_retries_evaluate(
    *,
    dispatch_id: str,
    attempt: int,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence, deliver one dispatch, and evaluate whether
    it should be retried at the given attempt number.

    Exit codes: 0 the decision was produced, 2 it could not be
    (unknown dispatch, missing notification/channel, or no delivery
    policy configured for the channel).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        decision = _evaluate_dispatch(runtime, dispatch_id, attempt)

    except Exception as exc:
        _render_retry_failure(exc, json_output=json_output, stderr=stderr)

        return 2

    if json_output:
        json.dump(
            decision.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Retry Decision\n\n")

        _write_decision_fields(decision, stdout=stdout)

    return 0


def run_deployment_governance_retries_preview(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and preview the retry decision for one
    dispatch at its first attempt (attempt 0).

    Exit codes: 0 the decision was produced, 2 it could not be
    (unknown dispatch, missing notification/channel, or no delivery
    policy configured for the channel).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        decision = _evaluate_dispatch(runtime, dispatch_id, 0)

    except Exception as exc:
        _render_retry_failure(exc, json_output=json_output, stderr=stderr)

        return 2

    if json_output:
        json.dump(
            decision.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Retry Preview\n\n")

        _write_decision_fields(decision, stdout=stdout)

    return 0


def _write_decision_fields(
    decision: GovernanceIntegrityRetryDecision,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Should retry: {decision.should_retry}\n")

    stdout.write(f"Retry attempt: {decision.retry_attempt}\n")

    if decision.next_retry_at is not None:
        stdout.write(
            f"Next retry at: {decision.next_retry_at.isoformat()}\n"
        )

    if decision.delay_seconds is not None:
        stdout.write(f"Delay seconds: {decision.delay_seconds}\n")

    if decision.reason is not None:
        stdout.write(f"Reason: {decision.reason}\n")


def _render_retry_failure(
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
        "Governance audit retry operation could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
