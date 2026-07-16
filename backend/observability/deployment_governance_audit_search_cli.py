from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_history_service import (
    serialize_governance_integrity_audit_record,
)
from .deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchQuery,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_search(
    *,
    audit_id: str | None = None,
    healthy: bool | None = None,
    label: str | None = None,
    bookmark: str | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and search audit history by filter.

    This is a read-only inspection command: it never executes a new
    audit and never mutates audit history, labels, or bookmarks. At
    least one filter is required. Exit codes: 0 the search completed
    (even with zero matches), 2 the search could not be completed (no
    filter supplied, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        query = GovernanceIntegrityAuditSearchQuery(
            audit_id=audit_id,
            healthy=healthy,
            label=label,
            bookmark=bookmark,
        )

        records = (
            runtime
            .build_integrity_audit_search_service()
            .search(query)
        )

    except Exception as exc:
        _render_search_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_search_json(records, stdout=stdout)

    else:
        _render_search_human(records, stdout=stdout)

    return 0


def _render_search_human(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Audit Search\n")

    stdout.write("=======================\n\n")

    if not records:
        stdout.write("No matching audits found.\n")

        return

    stdout.write(f"Matches: {len(records)}\n\n")

    for index, record in enumerate(records):
        stdout.write(f"{record.audit_id}\n")

        stdout.write(f"{record.outcome.value.upper()}\n")

        if index < len(records) - 1:
            stdout.write("\n")


def _render_search_json(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [
            serialize_governance_integrity_audit_record(record)
            for record in records
        ],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_search_failure(
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
        "Governance audit search could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
