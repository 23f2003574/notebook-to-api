from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_failure_policy import (
    GovernanceIntegrityFailureAction,
    GovernanceIntegrityFailurePolicy,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_failure_policy_create(
    *,
    name: str,
    action: str,
    max_retry_attempts: int,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new failure policy.

    Exit codes: 0 the policy was created, 2 the policy could not be
    created (duplicate name, invalid action, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = (
            runtime
            .build_integrity_failure_policy_service()
            .create(
                name,
                GovernanceIntegrityFailureAction(action),
                max_retry_attempts,
            )
        )

    except Exception as exc:
        _render_failure_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_json(policy, stdout=stdout)

    else:
        stdout.write("Policy created\n\n")

        _write_policy_fields(policy, stdout=stdout)

    return 0


def run_deployment_governance_failure_policy_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every failure policy.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policies = (
            runtime
            .build_integrity_failure_policy_service()
            .list()
        )

    except Exception as exc:
        _render_failure_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_list_json(policies, stdout=stdout)

    else:
        stdout.write("Failure Policies\n")

        stdout.write("================\n\n")

        if not policies:
            stdout.write(
                "No governance audit failure policies are configured.\n"
            )

        else:
            for policy in policies:
                stdout.write(f"{policy.name}\n")

    return 0


def run_deployment_governance_failure_policy_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one failure policy.

    Exit codes: 0 the policy was found, 2 the policy could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = (
            runtime
            .build_integrity_failure_policy_service()
            .get(name)
        )

        if policy is None:
            raise KeyError(
                f"failure policy '{name}' was not found"
            )

    except Exception as exc:
        _render_failure_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_json(policy, stdout=stdout)

    else:
        stdout.write("Policy\n\n")

        _write_policy_fields(policy, stdout=stdout)

    return 0


def run_deployment_governance_failure_policy_update(
    *,
    name: str,
    action: str | None = None,
    max_retry_attempts: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and update an existing failure policy.

    Exit codes: 0 the policy was updated, 2 the policy could not be
    updated (unknown name, invalid action, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = (
            runtime
            .build_integrity_failure_policy_service()
            .update(
                name,
                action=(
                    None
                    if action is None
                    else GovernanceIntegrityFailureAction(action)
                ),
                max_retry_attempts=max_retry_attempts,
            )
        )

    except Exception as exc:
        _render_failure_policy_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_policy_json(policy, stdout=stdout)

    else:
        stdout.write("Policy updated\n\n")

        _write_policy_fields(policy, stdout=stdout)

    return 0


def run_deployment_governance_failure_policy_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one failure policy.

    Exit codes: 0 the policy was deleted, 2 the policy could not be
    deleted (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_failure_policy_service().delete(name)

    except Exception as exc:
        _render_failure_policy_failure(
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
        stdout.write(f"Policy '{name}' deleted.\n")

    return 0


def _write_policy_fields(
    policy: GovernanceIntegrityFailurePolicy,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Name: {policy.name}\n")

    stdout.write(f"Action: {policy.action.value}\n")

    stdout.write(f"Max retry attempts: {policy.max_retry_attempts}\n")


def _render_policy_json(
    policy: GovernanceIntegrityFailurePolicy,
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
    policies: tuple[GovernanceIntegrityFailurePolicy, ...],
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


def _render_failure_policy_failure(
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
        "Governance audit failure policy operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
