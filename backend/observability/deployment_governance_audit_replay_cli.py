from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_replay import (
    GovernanceIntegrityAuditReplay,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_replay(
    *,
    audit_id: str | None = None,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and reconstruct one or more stored audits.

    This is a read-only inspection command: it never executes a new audit
    and never mutates audit history. Selection precedence is --audit-id,
    then --limit, then the latest audit (the default with no flags).
    Exit codes: 0 the replay succeeded, 2 the replay could not be
    completed (unknown audit id, empty history, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_audit_replay_service()

        if audit_id is not None:
            replay: GovernanceIntegrityAuditReplay | None = (
                service.replay(audit_id)
            )
            replays = None

        elif limit is not None:
            replay = None
            replays = service.replay_recent(limit=limit)

        else:
            replay = service.replay_latest()
            replays = None

    except Exception as exc:
        _render_replay_failure(exc, json_output=json_output, stderr=stderr)

        return 2

    if replay is not None:
        if json_output:
            _render_replay_json(replay, stdout=stdout)

        else:
            _render_replay_human(replay, stdout=stdout)

    else:
        assert replays is not None

        if json_output:
            _render_replay_list_json(replays, stdout=stdout)

        else:
            _render_replay_list_human(replays, stdout=stdout)

    return 0


def _render_replay_human(
    replay: GovernanceIntegrityAuditReplay,
    *,
    stdout: TextIO,
) -> None:
    record = replay.record

    stdout.write("Governance Audit Replay\n")

    stdout.write("=======================\n\n")

    stdout.write(f"Audit ID: {replay.audit_id}\n")

    stdout.write(f"Healthy: {'yes' if record.healthy else 'no'}\n")

    stdout.write(f"Started: {record.started_at.isoformat()}\n")

    stdout.write(f"Completed: {record.completed_at.isoformat()}\n")

    stdout.write("\n")

    stdout.write(f"Records Checked: {record.total_records}\n")

    stdout.write(f"Invalid Records: {record.invalid_records}\n")

    stdout.write(
        f"Integrity Mismatches: {record.integrity_mismatches}\n"
    )


def _render_replay_list_human(
    replays: tuple[GovernanceIntegrityAuditReplay, ...],
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Replay History\n")

    stdout.write("==============\n\n")

    for index, replay in enumerate(replays, start=1):
        stdout.write(f"{index}. {replay.audit_id}\n")


def _render_replay_json(
    replay: GovernanceIntegrityAuditReplay,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        replay.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_replay_list_json(
    replays: tuple[GovernanceIntegrityAuditReplay, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [replay.to_dict() for replay in replays],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_replay_failure(
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
        "Governance audit replay could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
