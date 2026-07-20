from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import TextIO

from .deployment_governance_logging import GovernanceLogEntry
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_logging_tail(
    *,
    level: str | None = None,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and render the most recently buffered
    governance log entries, newest first.

    This is a read-only inspection command: it never emits a new log
    entry and never mutates logged history. Exit codes: 0 the log
    tail was produced successfully (even if empty), 2 it could not
    be (including an invalid --level).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        entries = runtime.build_integrity_logger().entries(
            limit=limit,
            level=None if level is None else level.upper(),
        )

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_logging_json(entries, stdout=stdout)

    else:
        _render_logging_human(entries, stdout=stdout)

    return 0


def run_deployment_governance_logging_list(
    *,
    level: str | None = None,
    component: str | None = None,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and render the durable governance log
    history, oldest first.

    Unlike `logs tail`, which reads the logger's in-process buffer,
    this reads the underlying log repository directly: under the
    SQLite backend, that history survives process restarts. This is
    a read-only inspection command: it never emits a new log entry
    and never mutates logged history. Exit codes: 0 the log listing
    was produced successfully (even if empty), 2 it could not be
    (including an invalid --level).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        entries = runtime.build_integrity_log_repository().list(
            level=None if level is None else level.upper(),
            component=component,
            limit=limit,
        )

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_logging_json(entries, stdout=stdout)

    else:
        _render_logging_human(entries, stdout=stdout)

    return 0


def run_deployment_governance_logging_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and discard every entry in the durable
    governance log repository.

    Exit codes: 0 the log repository was cleared (even if it was
    already empty), 2 it could not be cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        repository = runtime.build_integrity_log_repository()

        discarded = len(repository.list())

        repository.clear()

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"discarded": discarded},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Cleared {discarded} governance log "
            f"entr{'y' if discarded == 1 else 'ies'}.\n"
        )

    return 0


def run_deployment_governance_logging_rotate(
    *,
    max_entries: int | None = None,
    max_age: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and run governance log rotation now,
    discarding entries outside the configured policy.

    --max-entries/--max-age override the configured policy for this
    invocation only; the change is not persisted beyond it. Exit
    codes: 0 rotation ran (even if nothing was discarded), 2 it
    could not run.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        rotation_service = (
            runtime.build_integrity_log_rotation_service()
        )

        if max_entries is not None:
            rotation_service.reconfigure(max_entries=max_entries)

        if max_age is not None:
            rotation_service.reconfigure(max_age_days=max_age)

        discarded = rotation_service.rotate()

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"discarded": discarded},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Governance log rotation discarded {discarded} "
            f"entr{'y' if discarded == 1 else 'ies'}.\n"
        )

    return 0


def run_deployment_governance_logging_rotation_status(
    *,
    max_entries: int | None = None,
    max_age: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the configured governance log
    rotation policy, without discarding anything.

    --max-entries/--max-age preview a different policy for this
    invocation only; the change is not persisted and rotation never
    runs. Exit codes: 0 the policy was retrieved, 2 it could not be.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        rotation_service = (
            runtime.build_integrity_log_rotation_service()
        )

        if max_entries is not None:
            rotation_service.reconfigure(max_entries=max_entries)

        if max_age is not None:
            rotation_service.reconfigure(max_age_days=max_age)

        policy = rotation_service.policy()

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            policy.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Log Rotation Policy\n\n")

        stdout.write(f"Max Entries: {policy.max_entries}\n")

        stdout.write(
            "Max Age (days): "
            + (
                "disabled"
                if policy.max_age_days is None
                else str(policy.max_age_days)
            )
            + "\n"
        )

    return 0


def run_deployment_governance_logging_search(
    *,
    level: str | None = None,
    component: str | None = None,
    event: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
    offset: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and search the durable governance log
    history, newest first.

    Every given filter combines with AND; since/until form an
    inclusive time range. This is a read-only inspection command: it
    never emits a new log entry and never mutates logged history.
    Exit codes: 0 the search was produced (even if empty), 2 it
    could not be (including an invalid --level).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        search_service = (
            runtime.build_integrity_log_search_service()
        )

        entries = search_service.search(
            level=None if level is None else level.upper(),
            component=component,
            event=event,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_logging_json(entries, stdout=stdout)

    else:
        _render_logging_human(entries, stdout=stdout)

    return 0


def _render_logging_human(
    entries: tuple[GovernanceLogEntry, ...],
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Logs\n")

    stdout.write("================\n\n")

    if not entries:
        stdout.write(
            "No governance log entries have been recorded.\n"
        )

        return

    for entry in entries:
        fields = " ".join(
            f"{key}={value}"
            for key, value in entry.fields.items()
        )

        stdout.write(
            f"{entry.timestamp.isoformat()} "
            f"[{entry.level}] "
            f"{entry.component}: {entry.event}"
            + (f" ({fields})" if fields else "")
            + "\n"
        )


def _render_logging_json(
    entries: tuple[GovernanceLogEntry, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [entry.to_dict() for entry in entries],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_logging_failure(
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
        "Governance log operation could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
