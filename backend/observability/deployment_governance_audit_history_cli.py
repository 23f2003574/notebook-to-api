from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import TextIO

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from .deployment_governance_audit_history_service import (
    GovernanceIntegrityAuditHistoryResult,
)
from .deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionSnapshot,
)
from .deployment_governance_audit_trends import (
    GovernanceIntegrityAuditTrendSnapshot,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_delivery_runtime import (
    GovernanceIntegrityRuntimeState,
    GovernanceIntegrityRuntimeStatus,
    GovernanceIntegrityDeliveryRuntime,
    build_integrity_delivery_runtime,
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

    include_trend: bool = False

    trend_window: int = 20

    include_regression: bool = False

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

        if self.trend_window <= 0:
            raise ValueError(
                "trend_window must be greater than zero"
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
    include_trend: bool = False,
    trend_window: int = 20,
    include_regression: bool = False,
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
            include_trend=include_trend,
            trend_window=trend_window,
            include_regression=include_regression,
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

        trend = (
            None
            if not options.include_trend
            else (
                runtime
                .build_integrity_audit_trend_service()
                .analyze(window=options.trend_window)
            )
        )

        regression = (
            None
            if not options.include_regression
            else (
                runtime
                .build_integrity_regression_service()
                .detect()
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
        _render_json(
            result,
            trend=trend,
            regression=regression,
            stdout=stdout,
        )

    else:
        _render_human(
            result,
            trend=trend,
            regression=regression,
            stdout=stdout,
        )

    return int(GovernanceAuditHistoryExitCode.SUCCESS)


def _render_json(
    result: GovernanceIntegrityAuditHistoryResult,
    *,
    trend: GovernanceIntegrityAuditTrendSnapshot | None,
    regression: GovernanceIntegrityRegressionSnapshot | None,
    stdout: TextIO,
) -> None:
    """
    Render machine-readable audit history.

    Only JSON is written to stdout so `... | jq` style piping stays valid.
    The "trend" and "regression" keys are only present when requested, so
    the plain `audits --json` schema stays exactly as it was before trend
    and regression analysis existed.
    """

    payload = result.to_dict()

    if trend is not None:
        payload["trend"] = trend.to_dict()

    if regression is not None:
        payload["regression"] = regression.to_dict()

    json.dump(
        payload,
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_human(
    result: GovernanceIntegrityAuditHistoryResult,
    *,
    trend: GovernanceIntegrityAuditTrendSnapshot | None,
    regression: GovernanceIntegrityRegressionSnapshot | None,
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

    else:
        stdout.write("\n")

        _write_audit_records(result.records, stdout=stdout)

    if trend is not None:
        stdout.write("\n")

        _write_trend_section(trend, stdout=stdout)

    if regression is not None:
        _write_regression_section(regression, stdout=stdout)


def _write_audit_records(
    records: tuple[GovernanceIntegrityAuditRecord, ...],
    *,
    stdout: TextIO,
) -> None:
    for index, record in enumerate(records, start=1):
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

        if index < len(records):
            stdout.write("\n")


def _write_trend_section(
    trend: GovernanceIntegrityAuditTrendSnapshot,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Trend Analysis\n")

    stdout.write("--------------\n")

    stdout.write(f"Sample size: {trend.sample_size}\n")

    stdout.write(f"Direction: {trend.direction.value.upper()}\n")

    stdout.write(
        "Current outcome: "
        + (
            "not available"
            if trend.current_outcome is None
            else trend.current_outcome.value.upper()
        )
        + "\n"
    )

    if trend.previous_outcome is not None:
        stdout.write(
            f"Previous outcome: {trend.previous_outcome.value.upper()}\n"
        )

    stdout.write(f"Current streak: {trend.current_streak}\n")

    stdout.write(
        "Health rate: "
        + (
            "not available"
            if trend.health_rate is None
            else f"{trend.health_rate * 100:.2f}%"
        )
        + "\n"
    )

    stdout.write(
        "Failure rate: "
        + (
            "not available"
            if trend.failure_rate is None
            else f"{trend.failure_rate * 100:.2f}%"
        )
        + "\n"
    )


def _format_signed_integer(value: int | None) -> str:
    if value is None:
        return "not available"

    if value > 0:
        return f"+{value}"

    return str(value)


def _write_regression_section(
    snapshot: GovernanceIntegrityRegressionSnapshot,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("\nRegression Analysis\n")

    stdout.write("-------------------\n")

    stdout.write(f"Status: {snapshot.status.value.upper()}\n")

    stdout.write(
        "Regression detected: "
        + ("yes" if snapshot.regression_detected else "no")
        + "\n"
    )

    if snapshot.baseline_audit_id is not None:
        stdout.write(f"Baseline audit: {snapshot.baseline_audit_id}\n")

    if snapshot.current_audit_id is not None:
        stdout.write(f"Current audit: {snapshot.current_audit_id}\n")

    if snapshot.baseline_outcome is not None:
        stdout.write(
            f"Baseline outcome: {snapshot.baseline_outcome.value.upper()}\n"
        )

    if snapshot.current_outcome is not None:
        stdout.write(
            f"Current outcome: {snapshot.current_outcome.value.upper()}\n"
        )

    if snapshot.invalid_record_delta is not None:
        stdout.write(
            "Invalid record delta: "
            f"{_format_signed_integer(snapshot.invalid_record_delta)}\n"
        )

    if snapshot.newly_introduced_failure_categories:
        stdout.write("\nNew failure categories:\n")

        for category in snapshot.newly_introduced_failure_categories:
            stdout.write(f"  {category}\n")


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


class GovernanceDeliveryRuntimeExitCode(IntEnum):
    """
    Exit codes produced by the governance delivery runtime commands.
    """

    SUCCESS = 0

    RUNTIME_ERROR = 1

    INVALID_STATE = 2


def run_delivery_runtime_start(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Start the governance delivery runtime.
    """

    try:

        from .deployment_governance_delivery_worker import (
            GovernanceIntegrityDeliveryWorker,
        )
        from .deployment_governance_delivery_scheduler import (
            GovernanceIntegrityDeliveryScheduler,
        )
        from .deployment_governance_provider_registry import (
            GovernanceIntegrityProviderRegistry,
        )

        scheduler = GovernanceIntegrityDeliveryScheduler()
        provider_registry = GovernanceIntegrityProviderRegistry()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler=scheduler,
            delivery_engine=None,
            retry_orchestrator=None,
        )

        runtime = build_integrity_delivery_runtime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry
        )

        runtime.start()

        status = runtime.status()

    except Exception as exc:
        _render_runtime_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceDeliveryRuntimeExitCode.RUNTIME_ERROR)

    if json_output:
        _render_runtime_status_json(status, stdout=stdout)
    else:
        _render_runtime_status_human(status, stdout=stdout)

    return int(GovernanceDeliveryRuntimeExitCode.SUCCESS)


def run_delivery_runtime_stop(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Stop the governance delivery runtime.
    """

    try:

        from .deployment_governance_delivery_worker import (
            GovernanceIntegrityDeliveryWorker,
        )
        from .deployment_governance_delivery_scheduler import (
            GovernanceIntegrityDeliveryScheduler,
        )
        from .deployment_governance_provider_registry import (
            GovernanceIntegrityProviderRegistry,
        )

        scheduler = GovernanceIntegrityDeliveryScheduler()
        provider_registry = GovernanceIntegrityProviderRegistry()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler=scheduler,
            delivery_engine=None,
            retry_orchestrator=None,
        )

        runtime = build_integrity_delivery_runtime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry
        )

        runtime.stop()

        status = runtime.status()

    except Exception as exc:
        _render_runtime_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceDeliveryRuntimeExitCode.RUNTIME_ERROR)

    if json_output:
        _render_runtime_status_json(status, stdout=stdout)
    else:
        _render_runtime_status_human(status, stdout=stdout)

    return int(GovernanceDeliveryRuntimeExitCode.SUCCESS)


def run_delivery_runtime_status(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Get the current status of the governance delivery runtime.
    """

    try:

        from .deployment_governance_delivery_worker import (
            GovernanceIntegrityDeliveryWorker,
        )
        from .deployment_governance_delivery_scheduler import (
            GovernanceIntegrityDeliveryScheduler,
        )
        from .deployment_governance_provider_registry import (
            GovernanceIntegrityProviderRegistry,
        )

        scheduler = GovernanceIntegrityDeliveryScheduler()
        provider_registry = GovernanceIntegrityProviderRegistry()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler=scheduler,
            delivery_engine=None,
            retry_orchestrator=None,
        )

        runtime = build_integrity_delivery_runtime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry
        )

        status = runtime.status()

    except Exception as exc:
        _render_runtime_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceDeliveryRuntimeExitCode.RUNTIME_ERROR)

    if json_output:
        _render_runtime_status_json(status, stdout=stdout)
    else:
        _render_runtime_status_human(status, stdout=stdout)

    return int(GovernanceDeliveryRuntimeExitCode.SUCCESS)


def run_delivery_runtime_run_once(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Run a single iteration of the governance delivery runtime.
    """

    try:

        from .deployment_governance_delivery_worker import (
            GovernanceIntegrityDeliveryWorker,
        )
        from .deployment_governance_delivery_scheduler import (
            GovernanceIntegrityDeliveryScheduler,
        )
        from .deployment_governance_provider_registry import (
            GovernanceIntegrityProviderRegistry,
        )

        scheduler = GovernanceIntegrityDeliveryScheduler()
        provider_registry = GovernanceIntegrityProviderRegistry()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler=scheduler,
            delivery_engine=None,
            retry_orchestrator=None,
        )

        runtime = build_integrity_delivery_runtime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry
        )

        runtime.start()

        runtime.run_iteration()

        status = runtime.status()

    except Exception as exc:
        _render_runtime_failure(
            exc,
            json_output=json_output,
            stderr=stderr,
        )

        return int(GovernanceDeliveryRuntimeExitCode.RUNTIME_ERROR)

    if json_output:
        _render_runtime_status_json(status, stdout=stdout)
    else:
        _render_runtime_status_human(status, stdout=stdout)

    return int(GovernanceDeliveryRuntimeExitCode.SUCCESS)


def _render_runtime_status_json(
    status: GovernanceIntegrityRuntimeStatus,
    *,
    stdout: TextIO,
) -> None:
    """
    Render runtime status as JSON.
    """

    json.dump(
        {
            "state": status.state.value,
            "started_at": (
                status.started_at.isoformat()
                if status.started_at
                else None
            ),
            "uptime_seconds": status.uptime_seconds,
            "worker_iterations": status.worker_iterations,
            "active_dispatches": status.active_dispatches,
        },
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_runtime_status_human(
    status: GovernanceIntegrityRuntimeStatus,
    *,
    stdout: TextIO,
) -> None:
    """
    Render runtime status as human-readable text.
    """

    stdout.write("Governance Delivery Runtime Status\n")
    stdout.write("================================\n")
    stdout.write(f"State: {status.state.value.upper()}\n")

    if status.started_at:
        stdout.write(f"Started at: {status.started_at.isoformat()}\n")
    else:
        stdout.write("Started at: not started\n")

    stdout.write(f"Uptime: {status.uptime_seconds}s\n")
    stdout.write(f"Worker iterations: {status.worker_iterations}\n")
    stdout.write(f"Active dispatches: {status.active_dispatches}\n")


def _render_runtime_failure(
    error: Exception,
    *,
    json_output: bool,
    stderr: TextIO,
) -> None:
    """
    Render a runtime operation failure.
    """

    if json_output:
        json.dump(
            {
                "status": "runtime_error",
                "error": str(error),
                "exit_code": int(
                    GovernanceDeliveryRuntimeExitCode.RUNTIME_ERROR
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
        "Governance delivery runtime operation failed.\n"
    )

    stderr.write(f"Reason: {error}\n")
