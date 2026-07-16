from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_timeline import (
    GovernanceIntegrityAuditTimelineEvent,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_timeline(
    *,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and render a chronological audit timeline.

    This is a read-only inspection command: it never executes a new
    audit and never mutates audit history. Exit codes: 0 the timeline
    was produced successfully (even for empty history), 2 the timeline
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        events = (
            runtime
            .build_integrity_audit_timeline_service()
            .timeline(limit=limit)
        )

    except Exception as exc:
        _render_timeline_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_timeline_json(events, stdout=stdout)

    else:
        _render_timeline_human(events, stdout=stdout)

    return 0


def _render_timeline_human(
    events: tuple[GovernanceIntegrityAuditTimelineEvent, ...],
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Audit Timeline\n")

    stdout.write("=========================\n\n")

    if not events:
        stdout.write(
            "No governance integrity audits have been recorded.\n"
        )

        return

    for index, event in enumerate(events):
        stdout.write(f"{event.started_at.isoformat()}\n")

        stdout.write(f"{event.state.value.upper()}\n")

        stdout.write(f"{event.audit_id}\n")

        if index < len(events) - 1:
            stdout.write("\n")


def _render_timeline_json(
    events: tuple[GovernanceIntegrityAuditTimelineEvent, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [event.to_dict() for event in events],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_timeline_failure(
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
        "Governance audit timeline could not be produced.\n"
    )

    stderr.write(f"Reason: {error}\n")
