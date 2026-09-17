from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_labels import (
    GovernanceIntegrityAuditLabel,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_label_add(
    *,
    audit_id: str,
    label: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and apply a label to an audit.

    Exit codes: 0 the label was applied, 2 the label could not be
    applied (unknown audit id, duplicate label, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        record = (
            runtime
            .build_integrity_audit_label_service()
            .add(audit_id, label)
        )

    except Exception as exc:
        _render_label_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_label_json(record, stdout=stdout)

    else:
        _render_label_added_human(record, stdout=stdout)

    return 0


def run_deployment_governance_audit_label_remove(
    *,
    audit_id: str,
    label: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove a label from an audit.

    Exit codes: 0 the label was removed, 2 the label could not be
    removed (not applied, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_label_service().remove(
            audit_id, label
        )

    except Exception as exc:
        _render_label_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "status": "removed",
                "audit_id": audit_id,
                "label": label,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Label '{label}' removed from audit '{audit_id}'.\n"
        )

    return 0


def run_deployment_governance_audit_label_show(
    *,
    audit_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show every label applied to one audit.

    Exit codes: 0 the labels were shown (even if empty), 2 the labels
    could not be shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        labels = (
            runtime
            .build_integrity_audit_label_service()
            .labels(audit_id)
        )

    except Exception as exc:
        _render_label_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            list(labels),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        _render_label_names_human(
            labels,
            heading="Audit Labels",
            underline=True,
            empty_message=(
                "No labels have been applied to this audit."
            ),
            stdout=stdout,
        )

    return 0


def run_deployment_governance_audit_label_search(
    *,
    label: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and search for every audit carrying a label.

    Exit codes: 0 the search completed (even with zero matches), 2 the
    search could not be completed.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        audit_ids = (
            runtime
            .build_integrity_audit_label_service()
            .audits(label)
        )

    except Exception as exc:
        _render_label_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            list(audit_ids),
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        _render_label_names_human(
            audit_ids,
            heading="Audits",
            underline=False,
            empty_message="No audits carry this label.",
            stdout=stdout,
        )

    return 0


def run_deployment_governance_audit_label_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every governance audit label.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        records = (
            runtime.build_integrity_audit_label_service().list()
        )

    except Exception as exc:
        _render_label_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [record.to_dict() for record in records],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Governance Audit Labels\n")

        stdout.write("========================\n\n")

        if not records:
            stdout.write(
                "No governance audit labels have been created.\n"
            )

        else:
            for record in records:
                stdout.write(
                    f"{record.audit_id}: {record.label}\n"
                )

    return 0


def _render_label_added_human(
    record: GovernanceIntegrityAuditLabel,
    *,
    stdout: TextIO,
) -> None:
    stdout.write("Label added\n\n")

    stdout.write(f"Audit: {record.audit_id}\n")

    stdout.write(f"Label: {record.label}\n")


def _render_label_names_human(
    names: tuple[str, ...],
    *,
    heading: str,
    empty_message: str,
    stdout: TextIO,
    underline: bool = True,
) -> None:
    stdout.write(f"{heading}\n")

    if underline:
        stdout.write(f"{'=' * len(heading)}\n")

    stdout.write("\n")

    if not names:
        stdout.write(f"{empty_message}\n")

        return

    for name in names:
        stdout.write(f"{name}\n")


def _render_label_json(
    record: GovernanceIntegrityAuditLabel,
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


def _render_label_failure(
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
        "Governance audit label operation could not be completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
