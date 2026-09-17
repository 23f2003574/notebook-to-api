from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_bookmarks import (
    GovernanceIntegrityAuditBookmark,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_bookmark_add(
    *,
    name: str,
    audit_id: str | None = None,
    use_latest: bool = False,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a named governance audit bookmark.

    When --audit-id is omitted (equivalent to --latest), the bookmark
    points at the most recently started audit. Exit codes: 0 the
    bookmark was created, 2 the bookmark could not be created (unknown
    audit id, duplicate name, empty history, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_audit_bookmark_service()

        if use_latest or audit_id is None:
            bookmark = service.bookmark_latest(name)

        else:
            bookmark = service.create(name, audit_id)

    except Exception as exc:
        _render_bookmark_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_bookmark_json(bookmark, stdout=stdout)

    else:
        _render_bookmark_created_human(bookmark, stdout=stdout)

    return 0


def run_deployment_governance_audit_bookmark_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every governance audit bookmark.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        bookmarks = (
            runtime.build_integrity_audit_bookmark_service().list()
        )

    except Exception as exc:
        _render_bookmark_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_bookmark_list_json(bookmarks, stdout=stdout)

    else:
        _render_bookmark_list_human(bookmarks, stdout=stdout)

    return 0


def run_deployment_governance_audit_bookmark_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one governance audit bookmark.

    Exit codes: 0 the bookmark was found, 2 the bookmark could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        bookmark = (
            runtime.build_integrity_audit_bookmark_service().get(name)
        )

        if bookmark is None:
            raise KeyError(
                f"governance audit bookmark '{name}' was not found"
            )

    except Exception as exc:
        _render_bookmark_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_bookmark_json(bookmark, stdout=stdout)

    else:
        _render_bookmark_show_human(bookmark, stdout=stdout)

    return 0


def run_deployment_governance_audit_bookmark_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one governance audit bookmark.

    Exit codes: 0 the bookmark was deleted, 2 the bookmark could not be
    deleted (unknown name or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_bookmark_service().delete(name)

    except Exception as exc:
        _render_bookmark_failure(
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
        stdout.write(f"Bookmark '{name}' deleted.\n")

    return 0


def _render_bookmark_created_human(
    bookmark: GovernanceIntegrityAuditBookmark,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Bookmark created\n\n")

    stdout.write(f"Name: {bookmark.name}\n")

    stdout.write(f"Audit: {bookmark.audit_id}\n")


def _render_bookmark_show_human(
    bookmark: GovernanceIntegrityAuditBookmark,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Bookmark\n\n")

    stdout.write(f"Name: {bookmark.name}\n")

    stdout.write(f"Audit: {bookmark.audit_id}\n")

    stdout.write(f"Created: {bookmark.created_at.isoformat()}\n")


def _render_bookmark_list_human(
    bookmarks: tuple[GovernanceIntegrityAuditBookmark, ...],
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Bookmarks\n")

    stdout.write("=========\n\n")

    if not bookmarks:
        stdout.write(
            "No governance audit bookmarks have been created.\n"
        )

        return

    for bookmark in bookmarks:
        stdout.write(f"{bookmark.name}\n")


def _render_bookmark_json(
    bookmark: GovernanceIntegrityAuditBookmark,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        bookmark.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_bookmark_list_json(
    bookmarks: tuple[GovernanceIntegrityAuditBookmark, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [bookmark.to_dict() for bookmark in bookmarks],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_bookmark_failure(
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
        "Governance audit bookmark operation could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
