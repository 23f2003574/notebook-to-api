from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from .deployment_governance_logging import GovernanceLogEntry
from .deployment_governance_log_context import GovernanceLogContext
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)

_REDACTION_TEST_SAMPLE_FIELDS = {
    "password": "hunter2",
    "token": "abc123",
    "secret": "s3cr3t",
    "api_key": "sk-live-xyz",
    "authorization": "Bearer abc.def.ghi",
    "cookie": "session=xyz",
    "username": "alice",
    "headers": {"Authorization": "Bearer nested-token"},
    "items": [{"token": "nested-list-token"}, "plain-string"],
}


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


def run_deployment_governance_logging_export_json(
    *,
    output_path: "str | Path",
    level: str | None = None,
    component: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export the durable governance log
    history matching the given filters to output_path as a single
    JSON array, streamed entry by entry.

    Exit codes: 0 the export was written (even if empty), 2 it
    could not be (including an invalid --level or an unwritable
    output path).
    """

    return _run_logging_export(
        lambda service, stream: service.export_json(
            stream,
            level=None if level is None else level.upper(),
            component=component,
            since=since,
            until=until,
        ),
        output_path=output_path,
        stdout=stdout,
        stderr=stderr,
    )


def run_deployment_governance_logging_export_csv(
    *,
    output_path: "str | Path",
    level: str | None = None,
    component: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export the durable governance log
    history matching the given filters to output_path as CSV,
    streamed entry by entry.

    Exit codes: 0 the export was written (even if empty), 2 it
    could not be (including an invalid --level or an unwritable
    output path).
    """

    return _run_logging_export(
        lambda service, stream: service.export_csv(
            stream,
            level=None if level is None else level.upper(),
            component=component,
            since=since,
            until=until,
        ),
        output_path=output_path,
        stdout=stdout,
        stderr=stderr,
        newline="",
    )


def run_deployment_governance_logging_export_ndjson(
    *,
    output_path: "str | Path",
    level: str | None = None,
    component: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export the durable governance log
    history matching the given filters to output_path as
    newline-delimited JSON, streamed entry by entry.

    Exit codes: 0 the export was written (even if empty), 2 it
    could not be (including an invalid --level or an unwritable
    output path).
    """

    return _run_logging_export(
        lambda service, stream: service.export_ndjson(
            stream,
            level=None if level is None else level.upper(),
            component=component,
            since=since,
            until=until,
        ),
        output_path=output_path,
        stdout=stdout,
        stderr=stderr,
    )


def _run_logging_export(
    export: "Callable[[object, TextIO], int]",
    *,
    output_path: "str | Path",
    stdout: TextIO,
    stderr: TextIO,
    newline: str | None = None,
) -> int:
    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        export_service = (
            runtime.build_integrity_log_export_service()
        )

        path = Path(output_path)

        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open(
            "w", encoding="utf-8", newline=newline
        ) as stream:
            count = export(export_service, stream)

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=False, stderr=stderr
        )

        return 2

    stdout.write(
        f"Exported {count} governance log "
        f"entr{'y' if count == 1 else 'ies'} to {path}.\n"
    )

    return 0


def run_deployment_governance_logging_redaction_rules(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list the currently registered
    governance log redaction rules.

    This is a read-only inspection command: it never registers,
    unregisters, or applies a rule. Exit codes: 0 the rules were
    retrieved, 2 they could not be.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        rules = (
            runtime
            .build_integrity_log_redaction_service()
            .list_rules()
        )

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [rule.to_dict() for rule in rules],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Log Redaction Rules\n\n")

        if not rules:
            stdout.write("No redaction rules are configured.\n")

        else:
            for rule in rules:
                stdout.write(
                    f"{rule.field} -> {rule.replacement}\n"
                )

    return 0


def run_deployment_governance_logging_redaction_test(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show what the currently configured
    governance log redaction rules would do to a built-in sample
    payload covering every default-sensitive field name plus a
    nested example (a dict-in-a-dict and a list of dicts).

    This never logs, persists, or exports anything: it only runs
    the configured rules against the sample in memory, so it is
    safe to use to sanity-check a custom rule set. Exit codes: 0 the
    test ran, 2 it could not.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        redaction_service = (
            runtime.build_integrity_log_redaction_service()
        )

        sample_entry = GovernanceLogEntry(
            timestamp=datetime.now(timezone.utc),
            level="INFO",
            component="redaction-test",
            event="sample",
            fields=_REDACTION_TEST_SAMPLE_FIELDS,
        )

        redacted_entry = redaction_service.redact(sample_entry)

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "before": dict(sample_entry.fields),
                "after": dict(redacted_entry.fields),
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Log Redaction Test\n\n")

        stdout.write("Before:\n")

        stdout.write(
            json.dumps(
                sample_entry.fields, indent=2, ensure_ascii=False
            )
        )

        stdout.write("\n\nAfter:\n")

        stdout.write(
            json.dumps(
                redacted_entry.fields, indent=2, ensure_ascii=False
            )
        )

        stdout.write("\n")

    return 0


def run_deployment_governance_logging_context(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and demonstrate the governance log
    execution context service's nested push/pop scoping.

    Each CLI invocation is a fresh process, so there is never a live
    delivery-pipeline context to inspect here (the "before" state is
    always empty); to see real context values, log something with
    `logs tail`/`logs search` while a delivery worker iteration is
    actually running, since those entries have request_id/
    dispatch_id/provider merged into their fields automatically.
    This command instead pushes two sample nested scopes and reports
    current() at each step, to show the mechanics without needing a
    live delivery pipeline. Nothing is logged, persisted, or
    exported. Exit codes: 0 the demonstration ran, 2 it could not.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        context_service = (
            runtime.build_integrity_log_context_service()
        )

        before = context_service.current()

        outer = GovernanceLogContext(
            request_id="req-1",
            dispatch_id=None,
            provider=None,
            component="delivery_runtime",
        )

        context_service.push(outer)

        during_outer_scope = context_service.current()

        inner = GovernanceLogContext(
            request_id=None,
            dispatch_id="dispatch-1",
            provider="webhook",
            component="delivery_engine",
        )

        context_service.push(inner)

        during_nested_scope = context_service.current()

        context_service.pop()

        after_inner_pop = context_service.current()

        context_service.pop()

        after_outer_pop = context_service.current()

    except Exception as exc:
        _render_logging_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    steps = (
        ("before", before),
        ("during_outer_scope", during_outer_scope),
        ("during_nested_scope", during_nested_scope),
        ("after_inner_pop", after_inner_pop),
        ("after_outer_pop", after_outer_pop),
    )

    if json_output:
        json.dump(
            {
                name: (None if context is None else context.to_dict())
                for name, context in steps
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Log Context Demonstration\n\n")

        for name, context in steps:
            stdout.write(
                f"{name}: "
                + (
                    "no active context"
                    if context is None
                    else (
                        f"component={context.component} "
                        f"dispatch_id={context.dispatch_id} "
                        f"provider={context.provider} "
                        f"request_id={context.request_id}"
                    )
                )
                + "\n"
            )

    return 0


def run_deployment_governance_logging_trace(
    *,
    correlation_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show every durable log entry belonging
    to one traced operation, oldest first (chronological order, the
    natural way to read a trace).

    A match is either an entry whose own correlation_id equals
    correlation_id, or one whose parent_correlation_id does: passing
    a dispatch's root correlation_id returns every attempt's entries
    (each attempt gets its own child correlation under that root),
    while passing one specific attempt's correlation_id returns just
    that attempt's own entries. This scans the full durable log
    history, since correlation_id is not an indexed repository
    column. Exit codes: 0 the trace was produced (even if empty), 2
    it could not be.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        search_service = (
            runtime.build_integrity_log_search_service()
        )

        matches = tuple(
            entry
            for entry in search_service.iter_search()
            if entry.fields.get("correlation_id") == correlation_id
            or entry.fields.get("parent_correlation_id")
            == correlation_id
        )

        # iter_search() yields newest first; a trace reads naturally
        # oldest first, in the order the operation actually happened.
        entries = tuple(reversed(matches))

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
