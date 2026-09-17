from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_session import (
    GovernanceIntegrityAuditSession,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_session(
    *,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and reconstruct an ordered audit session.

    This is a read-only inspection command: it never executes a new
    audit and never mutates audit history. Exit codes: 0 the session
    was reconstructed successfully (even for empty history), 2 the
    session could not be reconstructed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        session = (
            runtime
            .build_integrity_audit_session_service()
            .session(limit=limit)
        )

    except Exception as exc:
        _render_session_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_session_json(session, stdout=stdout)

    else:
        _render_session_human(session, stdout=stdout)

    return 0


def _render_session_human(
    session: GovernanceIntegrityAuditSession,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Audit Session\n")

    stdout.write("========================\n\n")

    if session.total_audits == 0:
        stdout.write(
            "No governance integrity audits have been recorded.\n"
        )

        return

    stdout.write(f"Audits: {session.total_audits}\n\n")

    stdout.write(f"Latest : {session.latest_audit_id}\n")

    stdout.write(f"Oldest : {session.first_audit_id}\n\n")

    stdout.write("History\n\n")

    for index, record in enumerate(session.records, start=1):
        stdout.write(f"{index}. {record.audit_id}\n")


def _render_session_json(
    session: GovernanceIntegrityAuditSession,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        session.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_session_failure(
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
        "Governance audit session could not be reconstructed.\n"
    )

    stderr.write(f"Reason: {error}\n")
