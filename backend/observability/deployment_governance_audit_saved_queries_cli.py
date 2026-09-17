from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_saved_queries import (
    GovernanceIntegritySavedAuditQuery,
)
from .deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchQuery,
)
from .deployment_governance_audit_search_cli import (
    _render_search_human,
    _render_search_json,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_saved_query_save(
    *,
    name: str,
    audit_id: str | None = None,
    healthy: bool | None = None,
    label: str | None = None,
    bookmark: str | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and save a reusable search filter under a name.

    Exit codes: 0 the query was saved, 2 the query could not be saved
    (no filter supplied, duplicate name, or invalid configuration).
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

        saved_query = (
            runtime
            .build_integrity_saved_audit_query_service()
            .save(name, query)
        )

    except Exception as exc:
        _render_saved_query_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_saved_query_json(saved_query, stdout=stdout)

    else:
        stdout.write("Saved Query\n\n")

        stdout.write(f"Name: {saved_query.name}\n")

    return 0


def run_deployment_governance_audit_saved_query_run(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence, execute a saved query, and render its results
    identically to `governance audits search`.

    Exit codes: 0 the query executed (even with zero matches), 2 the
    query could not be executed (unknown name, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = (
            runtime
            .build_integrity_saved_audit_query_service()
            .execute(name)
        )

    except Exception as exc:
        _render_saved_query_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_search_json(records, stdout=stdout)

    else:
        _render_search_human(records, stdout=stdout)

    return 0


def run_deployment_governance_audit_saved_query_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every saved governance audit query.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        saved_queries = (
            runtime
            .build_integrity_saved_audit_query_service()
            .list()
        )

    except Exception as exc:
        _render_saved_query_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [
                saved_query.to_dict()
                for saved_query in saved_queries
            ],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Saved Queries\n")

        stdout.write("=============\n\n")

        if not saved_queries:
            stdout.write(
                "No governance audit queries have been saved.\n"
            )

        else:
            for saved_query in saved_queries:
                stdout.write(f"{saved_query.name}\n")

    return 0


def run_deployment_governance_audit_saved_query_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one saved governance audit query.

    Exit codes: 0 the query was found, 2 the query could not be found
    or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        saved_query = (
            runtime
            .build_integrity_saved_audit_query_service()
            .get(name)
        )

        if saved_query is None:
            raise KeyError(
                f"saved query '{name}' was not found"
            )

    except Exception as exc:
        _render_saved_query_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_saved_query_json(saved_query, stdout=stdout)

    else:
        _render_saved_query_show_human(saved_query, stdout=stdout)

    return 0


def run_deployment_governance_audit_saved_query_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one saved governance audit query.

    Exit codes: 0 the query was deleted, 2 the query could not be
    deleted (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_saved_audit_query_service().delete(
            name
        )

    except Exception as exc:
        _render_saved_query_failure(
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
        stdout.write(f"Saved query '{name}' deleted.\n")

    return 0


def _render_saved_query_show_human(
    saved_query: GovernanceIntegritySavedAuditQuery,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Saved Query\n\n")

    stdout.write(f"Name: {saved_query.name}\n")

    stdout.write(f"Query: {saved_query.query.to_dict()}\n")

    stdout.write(f"Created: {saved_query.created_at.isoformat()}\n")


def _render_saved_query_json(
    saved_query: GovernanceIntegritySavedAuditQuery,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        saved_query.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_saved_query_failure(
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
        "Governance audit saved query operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
