from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import TextIO

from .deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from .deployment_governance_metrics_aggregation import (
    GovernanceIntegrityMetricsAggregate,
    GovernanceIntegrityMetricsAggregationService,
)
from .deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricAlert,
)
from .deployment_governance_metrics_dashboard import (
    GovernanceIntegrityMetricsDashboard,
    GovernanceIntegrityMetricsDashboardService,
)
from .deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsSnapshot,
)
from .deployment_governance_metrics_middleware import (
    GovernanceIntegrityRequestMetrics,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def parse_governance_metrics_timestamp(
    value: str | None,
) -> datetime | None:
    """
    Parse an optional ISO-8601 governance metrics timestamp. Raises
    ValueError if the value is not valid ISO-8601 or is timezone
    naive.
    """

    if value is None:
        return None

    normalized = value.strip()

    if not normalized:
        raise ValueError("timestamp must not be empty")

    try:
        parsed = datetime.fromisoformat(normalized)

    except ValueError as exc:
        raise ValueError(
            "timestamp must be valid ISO-8601"
        ) from exc

    if parsed.tzinfo is None:
        raise ValueError(
            "timestamp must be timezone-aware"
        )

    return parsed


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


def run_deployment_governance_metrics_aggregate(
    *,
    start: str | None = None,
    end: str | None = None,
    hourly: bool = False,
    daily: bool = False,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and aggregate captured governance audit
    notification delivery metrics history over a time window.

    start/end are raw ISO-8601 strings (as received from the CLI's
    --from/--to flags); parsing failures are reported the same way
    as any other export failure. Without --hourly/--daily, both
    start and end must be given and a single aggregate for that
    exact window is produced. With --hourly or --daily, start/end
    are optional and default to the full range of captured history.

    Exit codes: 0 the aggregation succeeded, 2 it could not be
    computed (including passing both --hourly and --daily, or
    omitting --from/--to without either).
    """

    try:
        if hourly and daily:
            raise ValueError(
                "--hourly and --daily are mutually exclusive"
            )

        parsed_start = parse_governance_metrics_timestamp(start)
        parsed_end = parse_governance_metrics_timestamp(end)

        if (
            not hourly
            and not daily
            and (parsed_start is None or parsed_end is None)
        ):
            raise ValueError(
                "--from and --to are required unless --hourly or "
                "--daily is given"
            )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        aggregation_service = GovernanceIntegrityMetricsAggregationService(
            runtime.build_integrity_metrics_history_repository()
        )

        if hourly:
            aggregates = aggregation_service.hourly(
                start=parsed_start, end=parsed_end
            )

        elif daily:
            aggregates = aggregation_service.daily(
                start=parsed_start, end=parsed_end
            )

        else:
            aggregates = (
                aggregation_service.between(
                    parsed_start, parsed_end
                ),
            )

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_aggregates_json(aggregates, stdout=stdout)

    else:
        _render_aggregates_human(aggregates, stdout=stdout)

    return 0


def _render_aggregates_human(
    aggregates: tuple[GovernanceIntegrityMetricsAggregate, ...],
    *,
    stdout: TextIO,
) -> None:
    if not aggregates:
        stdout.write("No governance metrics activity in this range.\n")

        return

    stdout.write("Governance Delivery Metrics Aggregation\n\n")

    for aggregate in aggregates:
        stdout.write(
            f"Window: {aggregate.start.isoformat()} - "
            f"{aggregate.end.isoformat()}\n"
        )

        stdout.write(f"  Dispatches: {aggregate.dispatches}\n")

        stdout.write(f"  Successes: {aggregate.successes}\n")

        stdout.write(f"  Failures: {aggregate.failures}\n")

        stdout.write(f"  Retries: {aggregate.retries}\n")

        stdout.write(
            "  Average Duration: "
            f"{aggregate.average_duration_ms:.0f} ms\n\n"
        )


def _render_aggregates_json(
    aggregates: tuple[GovernanceIntegrityMetricsAggregate, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [aggregate.to_dict() for aggregate in aggregates],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def run_deployment_governance_metrics_alerts(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence, evaluate governance audit notification
    delivery metric alerts against the latest metrics snapshot, and
    show every currently active (triggered) alert.

    Exit codes: 0 the alerts were evaluated (even if none are
    active), 2 they could not be evaluated.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics_service = runtime.build_integrity_metrics_service()

        metrics_service.load()

        alert_service = runtime.build_integrity_metrics_alert_service()

        alert_service.evaluate(metrics_service.snapshot())

        alerts = alert_service.active()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_alerts_json(alerts, stdout=stdout)

    else:
        _render_alerts_human(alerts, stdout=stdout)

    return 0


def run_deployment_governance_metrics_alerts_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and dismiss every currently active
    governance audit notification delivery metric alert.

    Exit codes: 0 the alerts were cleared, 2 they could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        alert_service = runtime.build_integrity_metrics_alert_service()

        alert_service.clear()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "cleared"},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance metric alerts cleared.\n")

    return 0


def _render_alerts_human(
    alerts: tuple[GovernanceIntegrityMetricAlert, ...],
    *,
    stdout: TextIO,
) -> None:
    if not alerts:
        stdout.write("No active governance metric alerts.\n")

        return

    stdout.write("Governance Metric Alerts\n\n")

    for alert in alerts:
        stdout.write(f"Alert: {alert.name}\n")

        stdout.write(f"  Value: {alert.value}\n")

        stdout.write(f"  Threshold: {alert.threshold}\n")

        stdout.write(
            f"  Triggered At: {alert.triggered_at.isoformat()}\n\n"
        )


def _render_alerts_json(
    alerts: tuple[GovernanceIntegrityMetricAlert, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [alert.to_dict() for alert in alerts],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def run_deployment_governance_metrics_dashboard(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and build a compact governance audit
    notification delivery metrics dashboard: current counters,
    derived percentages, and active alert count.

    Resyncs metrics from durable storage and re-evaluates alerts
    before building the dashboard, since each CLI invocation starts
    with fresh, empty in-memory state.

    Exit codes: 0 the dashboard was built, 2 it could not be built.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        dashboard_service = GovernanceIntegrityMetricsDashboardService(
            runtime.build_integrity_metrics_service(),
            alert_service=(
                runtime.build_integrity_metrics_alert_service()
            ),
        )

        dashboard = dashboard_service.refresh()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            dashboard.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        _render_dashboard_human(dashboard, stdout=stdout)

    return 0


def _render_dashboard_human(
    dashboard: GovernanceIntegrityMetricsDashboard,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance Delivery Metrics Dashboard\n\n")

    stdout.write(
        f"Last Updated: {dashboard.last_updated.isoformat()}\n\n"
    )

    stdout.write(
        f"Total Dispatches: {dashboard.summary.total_dispatches}\n"
    )

    stdout.write(f"Success Rate: {dashboard.success_rate:.2f}%\n")

    stdout.write(f"Failure Rate: {dashboard.failure_rate:.2f}%\n")

    stdout.write(f"Retry Rate: {dashboard.retry_rate:.2f}%\n")

    stdout.write(f"Active Alerts: {dashboard.active_alerts}\n")


def run_deployment_governance_metrics_requests(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Show governance API request metrics: request count, response
    status breakdown, latency, and exceptions.

    These are collected in-process by
    GovernanceIntegrityMetricsMiddleware while a governance API
    server is running. A CLI invocation is always a separate
    process, so unless it happens to run in the same interpreter as
    a live server, this will report a fresh, empty collector rather
    than a running server's traffic.

    Exit codes: 0 the request metrics were retrieved, 2 they could
    not be retrieved.
    """

    try:
        from .deployment_governance_api import (
            get_request_metrics_collector,
        )

        metrics = get_request_metrics_collector().snapshot()

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            metrics.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        _render_request_metrics_human(metrics, stdout=stdout)

    return 0


def _render_request_metrics_human(
    metrics: GovernanceIntegrityRequestMetrics,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Governance API Request Metrics\n\n")

    stdout.write(f"Total Requests: {metrics.total_requests}\n")

    stdout.write(f"Successful: {metrics.successful_requests}\n")

    stdout.write(f"Failed: {metrics.failed_requests}\n")

    stdout.write(f"Exceptions: {metrics.exceptions}\n")

    stdout.write(
        f"Average Latency: {metrics.average_latency_ms:.2f} ms\n"
    )


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
