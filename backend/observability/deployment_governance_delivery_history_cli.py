from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_delivery_history import (
    GovernanceIntegrityDeliveryHistoryRecord,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_delivery_history_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every delivery history record.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = (
            runtime
            .build_integrity_delivery_history_service()
            .list()
        )

    except Exception as exc:
        _render_delivery_history_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_list_json(records, stdout=stdout)

    else:
        stdout.write("Delivery History\n")

        stdout.write("================\n\n")

        if not records:
            stdout.write(
                "No governance audit delivery history records are "
                "stored.\n"
            )

        else:
            for record in records:
                stdout.write(
                    f"{record.delivery_id}: {record.status.value}\n"
                )

    return 0


def run_deployment_governance_delivery_history_show(
    *,
    delivery_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one delivery history record.

    Exit codes: 0 the record was found, 2 the record could not be
    found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = (
            runtime
            .build_integrity_delivery_history_service()
            .get(delivery_id)
        )

        if record is None:
            raise KeyError(
                f"delivery history record '{delivery_id}' was not "
                "found"
            )

    except Exception as exc:
        _render_delivery_history_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_record_json(record, stdout=stdout)

    else:
        stdout.write("Delivery History Record\n\n")

        _write_record_fields(record, stdout=stdout)

    return 0


def run_deployment_governance_delivery_history_clear(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove every delivery history record.

    Exit codes: 0 the history was cleared, 2 the history could not be
    cleared.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_delivery_history_service().clear()

    except Exception as exc:
        _render_delivery_history_failure(
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
        stdout.write("Delivery history cleared.\n")

    return 0


def _write_record_fields(
    record: GovernanceIntegrityDeliveryHistoryRecord,
    *,
    stdout: TextIO,
) -> None:
    stdout.write(f"Delivery ID: {record.delivery_id}\n")

    stdout.write(f"Dispatch ID: {record.dispatch_id}\n")

    stdout.write(f"Channel: {record.channel_name}\n")

    stdout.write(f"Status: {record.status.value}\n")

    if record.error is not None:
        stdout.write(f"Error: {record.error}\n")

    stdout.write(f"Delivered: {record.delivered_at.isoformat()}\n")


def _render_record_json(
    record: GovernanceIntegrityDeliveryHistoryRecord,
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        record.to_dict(),
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_record_list_json(
    records: tuple[GovernanceIntegrityDeliveryHistoryRecord, ...],
    *,
    stdout: TextIO,
) -> None:
    json.dump(
        [record.to_dict() for record in records],
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_delivery_history_failure(
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
        "Governance audit delivery history operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
