from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_execution_metrics import (
    GovernanceIntegrityExecutionMetrics,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_execution_metrics(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and compute overall execution metrics.

    Exit codes: 0 the metrics were computed, 2 the metrics could not
    be computed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics = (
            runtime
            .build_integrity_execution_metrics_service()
            .compute()
        )

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


def run_deployment_governance_execution_metrics_for_template(
    *,
    template_name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and compute execution metrics for one
    template.

    Exit codes: 0 the metrics were computed, 2 the metrics could not
    be computed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        metrics = (
            runtime
            .build_integrity_execution_metrics_service()
            .compute_for_template(template_name)
        )

    except Exception as exc:
        _render_metrics_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_metrics_json(metrics, stdout=stdout)

    else:
        _render_metrics_human(
            metrics, stdout=stdout, template_name=template_name
        )

    return 0


def _render_metrics_human(
    metrics: GovernanceIntegrityExecutionMetrics,
    *,
    stdout: TextIO,
    template_name: str | None = None,
) -> None:
    stdout.write("Execution Metrics\n\n")

    if template_name is not None:
        stdout.write(f"Template: {template_name}\n")

    stdout.write(f"Runs: {metrics.total_runs}\n")

    stdout.write(f"Success: {metrics.successful_runs}\n")

    stdout.write(f"Failed: {metrics.failed_runs}\n")

    stdout.write(
        f"Success Rate: {metrics.success_rate * 100.0:.2f}%\n"
    )

    stdout.write(
        f"Average Runtime: {metrics.average_duration_ms:.0f} ms\n"
    )


def _render_metrics_json(
    metrics: GovernanceIntegrityExecutionMetrics,
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
        "Governance audit execution metrics could not be computed.\n"
    )

    stderr.write(f"Reason: {error}\n")
