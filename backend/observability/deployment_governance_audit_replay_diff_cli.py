from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_replay_diff import (
    GovernanceIntegrityAuditReplayDiff,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_diff(
    *,
    previous_audit_id: str | None = None,
    current_audit_id: str | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and compare two replayed audits.

    This is a read-only inspection command: it never executes a new audit
    and never mutates audit history. When both --previous and --current
    are omitted (the default, equivalent to --latest), the two most
    recently started audits are compared. Exit codes: 0 the diff
    succeeded, 2 the diff could not be completed (unknown audit id,
    insufficient history, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_audit_replay_diff_service()

        if (
            previous_audit_id is not None
            and current_audit_id is not None
        ):
            diff = service.compare(
                previous_audit_id,
                current_audit_id,
            )

        else:
            diff = service.compare_latest()

    except Exception as exc:
        _render_diff_failure(exc, json_output=json_output, stderr=stderr)

        return 2

    if json_output:
        _render_diff_json(diff, stdout=stdout)

    else:
        _render_diff_human(diff, stdout=stdout)

    return 0


def _render_diff_human(
    diff: GovernanceIntegrityAuditReplayDiff,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Audit Diff\n")

    stdout.write("=====================\n\n")

    stdout.write(f"Previous: {diff.previous_audit_id}\n")

    stdout.write(f"Current: {diff.current_audit_id}\n\n")

    if not diff.changed:
        stdout.write("No operational differences detected.\n")

        return

    stdout.write("Changed Fields\n")

    stdout.write("--------------\n\n")

    for index, field_diff in enumerate(diff.field_diffs):
        stdout.write(f"{field_diff.field}:\n")

        stdout.write(
            f"  {field_diff.previous} -> {field_diff.current}\n"
        )

        if index < len(diff.field_diffs) - 1:
            stdout.write("\n")


def _render_diff_json(
    diff: GovernanceIntegrityAuditReplayDiff,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        diff.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_diff_failure(
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
        "Governance audit diff could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
