from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from .deployment_governance_delivery_worker import (
    GovernanceIntegrityWorkerRunSummary,
)


def run_deployment_governance_delivery_worker_run(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and run a single delivery worker pass over
    the ready dispatch queue.

    Exit codes: 0 the run completed (even if some dispatches failed),
    2 the run itself could not be started.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        run_summary = (
            runtime.build_integrity_delivery_worker().run_once()
        )

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    _render_summary(run_summary, json_output=json_output, stdout=stdout)

    return 0


def run_deployment_governance_delivery_worker_summary(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show the most recent delivery worker
    run summary.

    Exit codes: 0 a summary was produced (a fresh worker with no
    prior run reports all-zero counters), 2 it could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        worker = runtime.build_integrity_delivery_worker()

        run_summary = worker.summary() or worker.run_once()

    except Exception as exc:
        _render_worker_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    _render_summary(run_summary, json_output=json_output, stdout=stdout)

    return 0


def _render_summary(
    run_summary: GovernanceIntegrityWorkerRunSummary,
    *,
    json_output: bool,
    stdout: TextIO,
) -> None:
    if json_output:
        json.dump(
            run_summary.to_dict(),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

        return

    stdout.write("Governance Audit Delivery Worker Run\n\n")

    stdout.write(f"Started at: {run_summary.started_at.isoformat()}\n")

    stdout.write(
        f"Finished at: {run_summary.finished_at.isoformat()}\n"
    )

    stdout.write(f"Processed: {run_summary.processed}\n")

    stdout.write(f"Succeeded: {run_summary.succeeded}\n")

    stdout.write(f"Failed: {run_summary.failed}\n")

    stdout.write(f"Retried: {run_summary.retried}\n")


def _render_worker_failure(
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
        "Governance audit delivery worker operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
