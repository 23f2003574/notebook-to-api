from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_delivery_engine import (
    GovernanceIntegrityDeliveryResult,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_delivery_run(
    *,
    dispatch_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and deliver one queued dispatch.

    Exit codes: 0 the dispatch was delivered (whether it succeeded or
    failed), 2 the dispatch could not be delivered (unknown dispatch,
    or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        result = (
            runtime
            .build_integrity_delivery_engine()
            .deliver(dispatch_id)
        )

    except Exception as exc:
        _render_delivery_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_result_json(result, stdout=stdout)

    else:
        _render_result_human(result, stdout=stdout)

    return 0


def run_deployment_governance_delivery_run_all(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and deliver every currently queued dispatch.

    Exit codes: 0 the run completed (even if no dispatches were
    queued), 2 the run could not be completed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        results = (
            runtime
            .build_integrity_delivery_engine()
            .deliver_all()
        )

    except Exception as exc:
        _render_delivery_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_result_list_json(results, stdout=stdout)

    else:
        stdout.write(f"Delivered {len(results)} dispatch(es)\n\n")

        for result in results:
            stdout.write(
                f"{result.dispatch_id}: {result.status.value}\n"
            )

    return 0


def _write_result_fields(
    result: GovernanceIntegrityDeliveryResult,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Dispatch ID: {result.dispatch_id}\n")

    stdout.write(f"Channel: {result.channel_name}\n")

    stdout.write(f"Status: {result.status.value}\n")

    if result.error is not None:
        stdout.write(f"Error: {result.error}\n")

    stdout.write(f"Delivered: {result.delivered_at.isoformat()}\n")


def _render_result_human(
    result: GovernanceIntegrityDeliveryResult,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Delivery result\n\n")

    _write_result_fields(result, stdout=stdout)


def _render_result_json(
    result: GovernanceIntegrityDeliveryResult,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        result.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_result_list_json(
    results: tuple[GovernanceIntegrityDeliveryResult, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [result.to_dict() for result in results],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_delivery_failure(
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
        "Governance audit notification delivery operation could not "
        "be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
