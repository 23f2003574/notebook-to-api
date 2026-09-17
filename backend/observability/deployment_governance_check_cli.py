from __future__ import annotations

import json
import sys
from enum import IntEnum
from typing import TextIO

from .deployment_governance_audit_retention import (
    governance_integrity_audit_automatic_retention_config_from_env,
)
from .deployment_governance_check import (
    GovernanceIntegrityCheckPolicy,
    GovernanceIntegrityCheckResult,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


class GovernanceIntegrityCheckExitCode(IntEnum):
    """
    Process exit codes for governance integrity checks.

    EXECUTION_FAILED (the check could not run) and POLICY_FAILED (the check
    ran but governance policy failed) are distinct on purpose so CI logs
    and shell automation can tell "broken pipeline" from "broken policy".
    """

    SUCCESS = 0

    EXECUTION_FAILED = 2

    POLICY_FAILED = 3


def run_deployment_governance_check(
    *,
    policy: GovernanceIntegrityCheckPolicy = (
        GovernanceIntegrityCheckPolicy.REGRESSION_ONLY
    ),
    batch_size: int = 500,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Execute and enforce one governance integrity check.

    This is the composition boundary: it reads environment configuration,
    builds the persistence runtime, and runs its integrity check service.
    It never decides backend selection or database paths itself.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env(),
            automatic_audit_retention=(
                governance_integrity_audit_automatic_retention_config_from_env()
            ),
        )

        result = (
            runtime
            .build_integrity_check_service()
            .check(
                policy=policy,
                batch_size=batch_size,
            )
        )

    except Exception as exc:
        _render_check_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceIntegrityCheckExitCode.EXECUTION_FAILED)

    if json_output:
        _render_check_json(result, stdout=stdout)

    else:
        _render_check_human(result, stdout=stdout)

    if result.passed:
        return int(GovernanceIntegrityCheckExitCode.SUCCESS)

    return int(GovernanceIntegrityCheckExitCode.POLICY_FAILED)


def _render_check_json(
    result: GovernanceIntegrityCheckResult,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        result.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_check_human(
    result: GovernanceIntegrityCheckResult,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Deployment Governance Integrity Check\n")

    stdout.write("=====================================\n")

    stdout.write(f"Status: {result.status.value.upper()}\n")

    stdout.write(f"Policy: {result.policy.value}\n")

    stdout.write(
        "Passed: " + ("yes" if result.passed else "no") + "\n"
    )

    stdout.write(f"Audit ID: {result.audit_id}\n")

    stdout.write(
        "Audit healthy: "
        + ("yes" if result.audit_healthy else "no")
        + "\n"
    )

    stdout.write("\nRegression Analysis\n")

    stdout.write("-------------------\n")

    stdout.write(
        f"Status: {result.regression.status.value.upper()}\n"
    )

    stdout.write(
        "Regression detected: "
        + ("yes" if result.regression.regression_detected else "no")
        + "\n"
    )

    if result.regression.baseline_audit_id is not None:
        stdout.write(
            f"Baseline audit: {result.regression.baseline_audit_id}\n"
        )

    if result.regression.current_audit_id is not None:
        stdout.write(
            f"Current audit: {result.regression.current_audit_id}\n"
        )

    if result.regression.newly_introduced_failure_categories:
        stdout.write("\nNew failure categories:\n")

        for category in (
            result.regression.newly_introduced_failure_categories
        ):
            stdout.write(f"  {category}\n")

    if result.retention is not None:
        stdout.write("\nAutomatic Retention\n")

        stdout.write("-------------------\n")

        stdout.write(
            "Applied: "
            + ("yes" if result.retention.applied else "no")
            + "\n"
        )

        stdout.write(
            f"Prunable records: {result.retention.plan.prunable_records}\n"
        )

        stdout.write(
            f"Deleted records: {result.retention.deleted_records}\n"
        )

        stdout.write(
            f"Records retained: {result.retention.plan.retained_records}\n"
        )


def _render_check_failure(
    error: Exception,
    *,
    json_output: bool,
    stderr: TextIO,
) -> None:
    if json_output:
        json.dump(
            {
                "status": "execution_failed",
                "passed": False,
                "error": str(error),
                "exit_code": int(
                    GovernanceIntegrityCheckExitCode.EXECUTION_FAILED
                ),
            },
            stderr,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stderr.write("\n")

        return

    stderr.write(
        "Governance integrity check could not be executed.\n"
    )

    stderr.write(f"Reason: {error}\n")
