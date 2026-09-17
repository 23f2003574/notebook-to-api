from __future__ import annotations

import json
import sys
from enum import IntEnum
from typing import TextIO

from .deployment_governance_audit_retention import (
    GovernanceIntegrityAuditPruningResult,
    GovernanceIntegrityAuditRetentionPolicy,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


class GovernanceAuditPruneExitCode(IntEnum):
    """
    Exit codes produced by the governance audit-history prune command.

    A dry-run that finds prunable records is not a failure: only invalid
    configuration or an execution error returns non-zero.
    """

    SUCCESS = 0

    EXECUTION_FAILED = 2


def run_deployment_governance_audit_prune(
    *,
    max_records: int | None = None,
    max_age_days: int | None = None,
    preserve_latest: bool = True,
    apply: bool = False,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and preview or apply an audit-history retention
    policy.

    This is the composition boundary: it reads environment configuration,
    builds the persistence runtime, and runs its retention service. It
    never decides backend selection or database paths itself.
    """

    try:
        policy = GovernanceIntegrityAuditRetentionPolicy(
            max_records=max_records,
            max_age_days=max_age_days,
            preserve_latest=preserve_latest,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        result = (
            runtime
            .build_integrity_audit_retention_service()
            .prune(policy, apply=apply)
        )

    except Exception as exc:
        _render_prune_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceAuditPruneExitCode.EXECUTION_FAILED)

    if json_output:
        _render_prune_json(result, stdout=stdout)

    else:
        _render_prune_human(result, stdout=stdout)

    return int(GovernanceAuditPruneExitCode.SUCCESS)


def _render_prune_json(
    result: GovernanceIntegrityAuditPruningResult,
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


def _render_prune_human(
    result: GovernanceIntegrityAuditPruningResult,
    *,
    stdout: TextIO,
) -> None:
    plan = result.plan

    stdout.write("Governance Integrity Audit Retention\n")

    stdout.write("====================================\n\n")

    stdout.write(
        "Mode: " + ("APPLIED" if result.applied else "DRY RUN") + "\n\n"
    )

    if result.applied:
        stdout.write(
            f"Total records before pruning: {plan.total_records}\n"
        )

    else:
        stdout.write(f"Total records: {plan.total_records}\n")

    stdout.write(f"Retained records: {plan.retained_records}\n")

    stdout.write(f"Prunable records: {plan.prunable_records}\n")

    stdout.write(f"Deleted records: {result.deleted_records}\n")

    if not plan.has_prunable_records:
        stdout.write(
            "\nNo audit records currently violate the retention policy.\n"
        )

    elif not result.applied:
        stdout.write(
            "\nNo records were deleted.\n"
            "Run again with --apply to execute this pruning plan.\n"
        )


def _render_prune_failure(
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
                "exit_code": int(
                    GovernanceAuditPruneExitCode.EXECUTION_FAILED
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
        "Governance integrity audit retention could not be evaluated.\n"
    )

    stderr.write(f"Reason: {error}\n")
