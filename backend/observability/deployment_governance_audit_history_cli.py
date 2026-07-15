from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import TextIO

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
)
from .deployment_governance_audit_history_service import (
    GovernanceIntegrityAuditHistoryResult,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


class GovernanceAuditHistoryExitCode(IntEnum):
    """
    Exit codes produced by the governance audit-history command.
    """

    SUCCESS = 0

    QUERY_FAILED = 2


@dataclass(frozen=True)
class GovernanceAuditHistoryOptions:
    """
    Options controlling audit-history inspection.
    """

    backend: str | None = None

    outcome: GovernanceIntegrityAuditOutcome | None = None

    started_at_or_after: datetime | None = None

    started_at_or_before: datetime | None = None

    limit: int = 20

    json_output: bool = False

    def __post_init__(self) -> None:
        if self.backend is not None and not self.backend.strip():
            raise ValueError(
                "backend must not be empty when provided"
            )

        if self.limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        if (
            self.started_at_or_after is not None
            and self.started_at_or_before is not None
            and self.started_at_or_after > self.started_at_or_before
        ):
            raise ValueError(
                "started_at_or_after must not be later "
                "than started_at_or_before"
            )


def parse_governance_audit_timestamp(
    value: str | None,
) -> datetime | None:
    """
    Parse an optional ISO-8601 audit-history timestamp.
    """

    if value is None:
        return None

    normalized = value.strip()

    if not normalized:
        raise ValueError("timestamp must not be empty")

    try:
        return datetime.fromisoformat(normalized)

    except ValueError as exc:
        raise ValueError(
            "timestamp must be valid ISO-8601"
        ) from exc


def run_deployment_governance_audit_history(
    *,
    backend: str | None = None,
    outcome: GovernanceIntegrityAuditOutcome | None = None,
    started_at_or_after: datetime | None = None,
    started_at_or_before: datetime | None = None,
    limit: int = 20,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and inspect recorded integrity audits.

    This is the composition boundary: it reads environment configuration,
    builds the persistence runtime, and queries its audit-history service.
    It never decides backend selection or database paths itself.
    """

    try:
        options = GovernanceAuditHistoryOptions(
            backend=backend,
            outcome=outcome,
            started_at_or_after=started_at_or_after,
            started_at_or_before=started_at_or_before,
            limit=limit,
            json_output=json_output,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        result = (
            runtime
            .build_integrity_audit_history_service()
            .search(
                backend=options.backend,
                outcome=options.outcome,
                started_at_or_after=options.started_at_or_after,
                started_at_or_before=options.started_at_or_before,
                limit=options.limit,
            )
        )

    except Exception as exc:
        _render_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceAuditHistoryExitCode.QUERY_FAILED)

    if json_output:
        _render_json(result, stdout=stdout)

    else:
        _render_human(result, stdout=stdout)

    return int(GovernanceAuditHistoryExitCode.SUCCESS)


def _render_json(
    result: GovernanceIntegrityAuditHistoryResult,
    *,
    stdout: TextIO,
) -> None:
    """
    Render machine-readable audit history.

    Only JSON is written to stdout so `... | jq` style piping stays valid.
    """

    json.dump(
        result.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_human(
    result: GovernanceIntegrityAuditHistoryResult,
    *,
    stdout: TextIO,
) -> None:
    """
    Render human-readable audit history.
    """

    summary = result.summary

    stdout.write(
        "Deployment Governance Integrity Audit History\n"
    )

    stdout.write(
        "=============================================\n"
    )

    stdout.write(f"Recorded audits: {summary.total_audits}\n")

    stdout.write(f"Healthy audits: {summary.healthy_audits}\n")

    stdout.write(f"Unhealthy audits: {summary.unhealthy_audits}\n")

    stdout.write(f"Returned audits: {len(result.records)}\n")

    if not result.records:
        stdout.write("\nNo matching integrity audits found.\n")

        return

    stdout.write("\n")

    for index, record in enumerate(result.records, start=1):
        stdout.write(f"Audit {index}\n")

        stdout.write("-------\n")

        stdout.write(f"ID: {record.audit_id}\n")

        stdout.write(f"Outcome: {record.outcome.value.upper()}\n")

        stdout.write(f"Backend: {record.backend}\n")

        stdout.write(f"Started: {record.started_at.isoformat()}\n")

        stdout.write(f"Completed: {record.completed_at.isoformat()}\n")

        stdout.write(f"Duration: {record.duration_seconds:.3f}s\n")

        stdout.write(f"Records scanned: {record.total_records}\n")

        stdout.write(f"Valid records: {record.valid_records}\n")

        stdout.write(f"Invalid records: {record.invalid_records}\n")

        if record.invalid_records > 0:
            stdout.write("Failure breakdown:\n")

            stdout.write(
                "  Integrity mismatches: "
                f"{record.integrity_mismatches}\n"
            )

            stdout.write(
                "  Missing integrity metadata: "
                f"{record.missing_integrity_metadata}\n"
            )

            stdout.write(
                "  Invalid integrity metadata: "
                f"{record.invalid_integrity_metadata}\n"
            )

            stdout.write(
                "  Invalid persisted records: "
                f"{record.invalid_persisted_records}\n"
            )

        if index < len(result.records):
            stdout.write("\n")


def _render_failure(
    error: Exception,
    *,
    json_output: bool,
    stderr: TextIO,
) -> None:
    """
    Render an audit-history query failure.
    """

    if json_output:
        json.dump(
            {
                "status": "query_failed",
                "error": str(error),
                "exit_code": int(
                    GovernanceAuditHistoryExitCode.QUERY_FAILED
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
        "Governance integrity audit history could not be inspected.\n"
    )

    stderr.write(f"Reason: {error}\n")
