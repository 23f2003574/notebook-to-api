from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertPolicy,
    GovernanceIntegrityExecutionAlert,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)

DEFAULT_MINIMUM_SUCCESS_RATE = 0.0

DEFAULT_MAXIMUM_FAILURE_RATE = 100.0

DEFAULT_MAXIMUM_AVERAGE_DURATION_MS = 1_000_000_000.0


def run_deployment_governance_execution_alerts(
    *,
    minimum_success_rate: float = DEFAULT_MINIMUM_SUCCESS_RATE,
    maximum_failure_rate: float = DEFAULT_MAXIMUM_FAILURE_RATE,
    maximum_average_duration_ms: float = (
        DEFAULT_MAXIMUM_AVERAGE_DURATION_MS
    ),
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and generate alerts from overall execution
    metrics.

    Exit codes: 0 alerts were generated (even if none were violated),
    2 alerts could not be generated.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = GovernanceIntegrityAlertPolicy(
            minimum_success_rate=minimum_success_rate,
            maximum_failure_rate=maximum_failure_rate,
            maximum_average_duration_ms=maximum_average_duration_ms,
        )

        alerts = (
            runtime
            .build_integrity_execution_alert_service()
            .generate(policy)
        )

    except Exception as exc:
        _render_alerts_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_alerts_json(alerts, stdout=stdout)

    else:
        _render_alerts_human(alerts, stdout=stdout)

    return 0


def run_deployment_governance_execution_alerts_for_template(
    *,
    template_name: str,
    minimum_success_rate: float = DEFAULT_MINIMUM_SUCCESS_RATE,
    maximum_failure_rate: float = DEFAULT_MAXIMUM_FAILURE_RATE,
    maximum_average_duration_ms: float = (
        DEFAULT_MAXIMUM_AVERAGE_DURATION_MS
    ),
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and generate alerts from one template's
    execution metrics.

    Exit codes: 0 alerts were generated (even if none were violated),
    2 alerts could not be generated.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        policy = GovernanceIntegrityAlertPolicy(
            minimum_success_rate=minimum_success_rate,
            maximum_failure_rate=maximum_failure_rate,
            maximum_average_duration_ms=maximum_average_duration_ms,
        )

        alerts = (
            runtime
            .build_integrity_execution_alert_service()
            .generate(policy, template_name=template_name)
        )

    except Exception as exc:
        _render_alerts_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_alerts_json(alerts, stdout=stdout)

    else:
        _render_alerts_human(
            alerts, stdout=stdout, template_name=template_name
        )

    return 0


def _render_alerts_human(
    alerts: tuple[GovernanceIntegrityExecutionAlert, ...],
    *,
    stdout: TextIO,
    template_name: str | None = None,
) -> None:
    stdout.write("Execution Alerts\n\n")

    if template_name is not None:
        stdout.write(f"Template: {template_name}\n\n")

    if not alerts:
        stdout.write(
            "No governance audit execution alerts.\n"
        )

        return

    for alert in alerts:
        stdout.write(
            f"[{alert.severity.value.upper()}] {alert.message}\n"
        )


def _render_alerts_json(
    alerts: tuple[GovernanceIntegrityExecutionAlert, ...],
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


def _render_alerts_failure(
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
        "Governance audit execution alerts could not be generated.\n"
    )

    stderr.write(f"Reason: {error}\n")
