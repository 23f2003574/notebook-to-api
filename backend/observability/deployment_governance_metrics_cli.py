from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from .deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsSnapshot,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_metrics(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and snapshot live governance audit
    notification delivery metrics.

    Exit codes: 0 the metrics were retrieved, 2 the metrics could
    not be retrieved.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.load()

        metrics = metrics_service.snapshot()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_metrics_json(metrics, stdout=stdout)

    else:
        _render_metrics_human(metrics, stdout=stdout)

    return 0


def run_deployment_governance_metrics_reset(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and clear live governance audit
    notification delivery metrics back to zero.

    Exit codes: 0 the metrics were reset, 2 the metrics could not be
    reset.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.reset()

        metrics = metrics_service.snapshot()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_metrics_json(metrics, stdout=stdout)

    else:
        stdout.write("Governance delivery metrics reset.\n\n")

        _render_metrics_human(metrics, stdout=stdout)

    return 0


def run_deployment_governance_metrics_export(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export the durably stored governance
    audit notification delivery metrics snapshot.

    Reads through the repository (via load()) rather than the
    process-local in-memory counters: each CLI invocation starts a
    fresh, empty metrics service, so exporting the in-memory state
    directly would always export zeroes and, if combined with a
    write, would clobber whatever a running delivery runtime had
    already persisted.

    Exit codes: 0 the metrics were exported, 2 the metrics could not
    be exported.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.load()

        metrics = metrics_service.snapshot()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_metrics_json(metrics, stdout=stdout)

    else:
        stdout.write("Governance delivery metrics exported.\n\n")

        _render_metrics_human(metrics, stdout=stdout)

    return 0


def run_deployment_governance_metrics_reload(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and replace live in-memory governance audit
    notification delivery metrics with whatever is durably stored.

    Exit codes: 0 the metrics were reloaded, 2 the metrics could not
    be reloaded.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.load()

        metrics = metrics_service.snapshot()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_metrics_json(metrics, stdout=stdout)

    else:
        stdout.write("Governance delivery metrics reloaded.\n\n")

        _render_metrics_human(metrics, stdout=stdout)

    return 0


def run_deployment_governance_metrics_history(
    *,
    limit: int | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list captured governance audit
    notification delivery metrics snapshots, newest first.

    Exit codes: 0 the history was retrieved, 2 the history could not
    be retrieved.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        snapshots = (
            runtime
            .build_integrity_metrics_service()
            .history(limit)
        )

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_history_json(snapshots, stdout=stdout)

    else:
        _render_history_human(snapshots, stdout=stdout)

    return 0


def run_deployment_governance_metrics_latest(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the most recently captured
    governance audit notification delivery metrics snapshot.

    Exit codes: 0 the latest snapshot was retrieved (even if none
    exists yet), 2 it could not be retrieved.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        snapshot = (
            runtime
            .build_integrity_metrics_service()
            .latest()
        )

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            snapshot.to_dict() if snapshot is not None else None,
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    elif snapshot is None:
        stdout.write("No governance metrics snapshots captured yet.\n")

    else:
        _render_history_human((snapshot,), stdout=stdout)

    return 0


def run_deployment_governance_metrics_export_json(
    *,
    include_history: bool = False,
    output_path: str | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export current governance audit
    notification delivery metrics (and optionally their captured
    history) as JSON, for offline analysis.

    Writes to output_path when given, otherwise to stdout.

    Exit codes: 0 the export succeeded, 2 it could not be exported.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.load()

        rendered = metrics_service.export_service().export_json(
            include_history=include_history
        )

        if output_path is not None:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(rendered)

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    _render_export_result(
        rendered,
        output_path=output_path,
        json_output=json_output,
        stdout=stdout,
    )

    return 0


def run_deployment_governance_metrics_export_csv(
    *,
    include_history: bool = False,
    output_path: str | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and export current governance audit
    notification delivery metrics (and optionally their captured
    history) as CSV, for offline analysis.

    Writes to output_path when given, otherwise to stdout.

    Exit codes: 0 the export succeeded, 2 it could not be exported.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.load()

        rendered = metrics_service.export_service().export_csv(
            include_history=include_history
        )

        if output_path is not None:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(rendered)

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    _render_export_result(
        rendered,
        output_path=output_path,
        json_output=json_output,
        stdout=stdout,
    )

    return 0


def _render_export_result(
    rendered: str,
    *,
    output_path: str | None,
    json_output: bool,
    stdout: TextIO,
) -> None:
    if output_path is not None:
        if json_output:
            json.dump(
                {"status": "exported", "output_path": output_path},
                stdout,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

            stdout.write("\n")

        else:
            stdout.write(f"Governance metrics exported to {output_path}\n")

        return

    stdout.write(rendered)

    if not rendered.endswith("\n"):
        stdout.write("\n")


def _render_history_human(
    snapshots: tuple[GovernanceIntegrityMetricsSnapshot, ...],
    *,
    stdout: TextIO,
) -> None:
    if not snapshots:
        stdout.write("No governance metrics snapshots captured yet.\n")

        return

    stdout.write("Governance Delivery Metrics History\n\n")

    for snapshot in snapshots:
        metrics = snapshot.metrics

        stdout.write(f"Captured At: {snapshot.captured_at.isoformat()}\n")

        stdout.write(f"  Total Dispatches: {metrics.total_dispatches}\n")

        stdout.write(f"  Successful: {metrics.successful_dispatches}\n")

        stdout.write(f"  Failed: {metrics.failed_dispatches}\n")

        stdout.write(f"  Retries: {metrics.retry_dispatches}\n")

        stdout.write(
            f"  Average Duration: {metrics.average_duration_ms:.0f} ms\n\n"
        )


def _render_history_json(
    snapshots: tuple[GovernanceIntegrityMetricsSnapshot, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [snapshot.to_dict() for snapshot in snapshots],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_metrics_human(
    metrics: GovernanceIntegrityMetrics,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Delivery Metrics\n\n")

    stdout.write(f"Total Dispatches: {metrics.total_dispatches}\n")

    stdout.write(f"Successful: {metrics.successful_dispatches}\n")

    stdout.write(f"Failed: {metrics.failed_dispatches}\n")

    stdout.write(f"Retries: {metrics.retry_dispatches}\n")

    stdout.write(
        f"Average Duration: {metrics.average_duration_ms:.0f} ms\n"
    )


def _render_metrics_json(
    metrics: GovernanceIntegrityMetrics,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        metrics.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_metrics_failure(
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
        "Governance delivery metrics could not be retrieved.\n"
    )

    stderr.write(f"Reason: {error}\n")
