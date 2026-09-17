from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_delivery_policy_create(
    *,
    channel_name: str,
    retry_limit: int,
    timeout_seconds: int,
    rate_limit_per_minute: int,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new delivery policy.

    Exit codes: 0 the policy was created, 2 the policy could not be
    created (unknown channel, duplicate policy, invalid values, or
    invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = (
            runtime
            .build_integrity_delivery_policy_service()
            .create(
                channel_name,
                retry_limit,
                timeout_seconds,
                rate_limit_per_minute,
            )
        )

    except Exception as exc:
        _render_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_json(policy, stdout=stdout)

    else:
        stdout.write("Policy created\n\n")

        _write_policy_fields(policy, stdout=stdout)

    return 0


def run_deployment_governance_delivery_policy_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every delivery policy.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policies = (
            runtime
            .build_integrity_delivery_policy_service()
            .list()
        )

    except Exception as exc:
        _render_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_list_json(policies, stdout=stdout)

    else:
        stdout.write("Delivery Policies\n")

        stdout.write("=================\n\n")

        if not policies:
            stdout.write(
                "No governance audit delivery policies are "
                "configured.\n"
            )

        else:
            for policy in policies:
                stdout.write(f"{policy.channel_name}\n")

    return 0


def run_deployment_governance_delivery_policy_show(
    *,
    channel_name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one delivery policy.

    Exit codes: 0 the policy was found, 2 the policy could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = (
            runtime
            .build_integrity_delivery_policy_service()
            .get(channel_name)
        )

        if policy is None:
            raise KeyError(
                f"delivery policy for channel '{channel_name}' was "
                "not found"
            )

    except Exception as exc:
        _render_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_json(policy, stdout=stdout)

    else:
        stdout.write("Policy\n\n")

        _write_policy_fields(policy, stdout=stdout)

    return 0


def run_deployment_governance_delivery_policy_update(
    *,
    channel_name: str,
    retry_limit: int | None = None,
    timeout_seconds: int | None = None,
    rate_limit_per_minute: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and update an existing delivery policy.

    Exit codes: 0 the policy was updated, 2 the policy could not be
    updated (unknown channel, invalid values, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = (
            runtime
            .build_integrity_delivery_policy_service()
            .update(
                channel_name,
                retry_limit=retry_limit,
                timeout_seconds=timeout_seconds,
                rate_limit_per_minute=rate_limit_per_minute,
            )
        )

    except Exception as exc:
        _render_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_json(policy, stdout=stdout)

    else:
        stdout.write("Policy updated\n\n")

        _write_policy_fields(policy, stdout=stdout)

    return 0


def run_deployment_governance_delivery_policy_delete(
    *,
    channel_name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one delivery policy.

    Exit codes: 0 the policy was deleted, 2 the policy could not be
    deleted (unknown channel, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_delivery_policy_service().delete(
            channel_name
        )

    except Exception as exc:
        _render_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "deleted", "channel_name": channel_name},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Policy for channel '{channel_name}' deleted.\n"
        )

    return 0


def _write_policy_fields(
    policy: GovernanceIntegrityDeliveryPolicy,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Channel: {policy.channel_name}\n")

    stdout.write(f"Retry limit: {policy.retry_limit}\n")

    stdout.write(f"Timeout seconds: {policy.timeout_seconds}\n")

    stdout.write(
        f"Rate limit per minute: {policy.rate_limit_per_minute}\n"
    )

    stdout.write(f"Enabled: {policy.enabled}\n")


def _render_policy_json(
    policy: GovernanceIntegrityDeliveryPolicy,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        policy.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_policy_list_json(
    policies: tuple[GovernanceIntegrityDeliveryPolicy, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [policy.to_dict() for policy in policies],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_policy_failure(
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
        "Governance audit delivery policy operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
