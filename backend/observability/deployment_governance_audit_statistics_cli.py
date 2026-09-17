from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditCurrentState,
    GovernanceIntegrityAuditStatisticsSnapshot,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_stats(
    *,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and render an operational audit-history summary.

    This is a read-only inspection command: it never executes a new
    audit and never mutates audit history. Exit codes: 0 the summary was
    produced successfully (even for empty history), 2 the summary could
    not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        snapshot = (
            runtime
            .build_integrity_audit_statistics_service()
            .calculate(limit=limit)
        )

    except Exception as exc:
        _render_stats_failure(exc, json_output=json_output, stderr=stderr)

        return 2

    if json_output:
        _render_stats_json(snapshot, stdout=stdout)

    else:
        _render_stats_human(snapshot, stdout=stdout)

    return 0


def _audit_count_label(count: int) -> str:
    return "audit" if count == 1 else "audits"


def _render_stats_human(
    snapshot: GovernanceIntegrityAuditStatisticsSnapshot,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Audit History Statistics\n")

    stdout.write("===================================\n\n")

    if snapshot.total_audits == 0:
        stdout.write(
            "No governance integrity audits have been recorded.\n"
        )

        return

    stdout.write(f"Audits: {snapshot.total_audits}\n")

    stdout.write(f"Healthy: {snapshot.healthy_audits}\n")

    stdout.write(f"Unhealthy: {snapshot.unhealthy_audits}\n")

    assert snapshot.health_rate is not None

    stdout.write(
        f"Health rate: {snapshot.health_rate * 100.0:.2f}%\n"
    )

    stdout.write("\n")

    stdout.write(
        f"Current state: {snapshot.current_state.value.upper()}\n"
    )

    current_streak_label = (
        "healthy"
        if (
            snapshot.current_state
            is GovernanceIntegrityAuditCurrentState.HEALTHY
        )
        else "unhealthy"
    )

    stdout.write(
        f"Current streak: {snapshot.current_streak} "
        f"{current_streak_label} "
        f"{_audit_count_label(snapshot.current_streak)}\n"
    )

    stdout.write(
        f"Longest healthy streak: {snapshot.longest_healthy_streak}\n"
    )

    stdout.write(
        "Longest unhealthy streak: "
        f"{snapshot.longest_unhealthy_streak}\n"
    )

    stdout.write("\n")

    stdout.write(
        f"First audit: {snapshot.first_audit_started_at.isoformat()}\n"
    )

    stdout.write(
        f"Latest audit: {snapshot.latest_audit_started_at.isoformat()}\n"
    )

    stdout.write("\nAggregate Audit Work\n")

    stdout.write("--------------------\n")

    stdout.write(
        f"Records checked: {snapshot.total_records_checked}\n"
    )

    stdout.write("\nAggregate Failures\n")

    stdout.write("------------------\n")

    stdout.write(
        f"Invalid records: {snapshot.total_invalid_records}\n"
    )

    stdout.write(
        "Integrity mismatches: "
        f"{snapshot.total_integrity_mismatches}\n"
    )

    stdout.write(
        "Missing integrity metadata: "
        f"{snapshot.total_missing_integrity_metadata}\n"
    )

    stdout.write(
        "Invalid integrity metadata: "
        f"{snapshot.total_invalid_integrity_metadata}\n"
    )


def _render_stats_json(
    snapshot: GovernanceIntegrityAuditStatisticsSnapshot,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        snapshot.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_stats_failure(
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
        "Governance audit history statistics could not be "
        "calculated.\n"
    )

    stderr.write(f"Reason: {error}\n")
