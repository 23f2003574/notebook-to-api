from __future__ import annotations

import json
import sys
from typing import TextIO

from .deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollection,
)
from .deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def run_deployment_governance_audit_collection_create(
    *,
    name: str,
    description: str | None = None,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and create a new governance audit collection.

    Exit codes: 0 the collection was created, 2 the collection could
    not be created (duplicate name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        collection = (
            runtime
            .build_integrity_audit_collection_service()
            .create(name, description)
        )

    except Exception as exc:
        _render_collection_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_collection_json(
            collection, audits=(), stdout=stdout
        )

    else:
        stdout.write("Collection created\n\n")

        stdout.write(f"Name: {collection.name}\n")

    return 0


def run_deployment_governance_audit_collection_list(
    *,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and list every governance audit collection.

    Exit codes: 0 the list was produced (even if empty), 2 the list
    could not be produced.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        collections = (
            runtime
            .build_integrity_audit_collection_service()
            .list()
        )

    except Exception as exc:
        _render_collection_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            [
                collection.to_dict()
                for collection in collections
            ],
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write("Collections\n")

        stdout.write("===========\n\n")

        if not collections:
            stdout.write(
                "No governance audit collections have been created.\n"
            )

        else:
            for collection in collections:
                stdout.write(f"{collection.name}\n")

    return 0


def run_deployment_governance_audit_collection_show(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and show one governance audit collection.

    Exit codes: 0 the collection was found, 2 the collection could not
    be found or shown.
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_audit_collection_service()

        collection = service.get(name)

        if collection is None:
            raise KeyError(
                f"collection '{name}' was not found"
            )

        audit_ids = service.audits(name)

    except Exception as exc:
        _render_collection_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        _render_collection_json(
            collection, audits=audit_ids, stdout=stdout
        )

    else:
        stdout.write("Collection\n\n")

        stdout.write(f"Name: {collection.name}\n")

        if collection.description is not None:
            stdout.write(
                f"Description: {collection.description}\n"
            )

        stdout.write("\nAudits\n\n")

        if not audit_ids:
            stdout.write(
                "No audits are in this collection.\n"
            )

        else:
            for audit_id in audit_ids:
                stdout.write(f"{audit_id}\n")

    return 0


def run_deployment_governance_audit_collection_delete(
    *,
    name: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and delete one governance audit collection.

    Deleting a collection also removes every entry in it. Exit codes: 0
    the collection was deleted, 2 the collection could not be deleted
    (unknown name, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_collection_service().delete(
            name
        )

    except Exception as exc:
        _render_collection_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {"status": "deleted", "name": name},
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(f"Collection '{name}' deleted.\n")

    return 0


def run_deployment_governance_audit_collection_add(
    *,
    name: str,
    audit_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and add an audit to a collection.

    Exit codes: 0 the audit was added, 2 the audit could not be added
    (unknown collection or audit, duplicate membership, or invalid
    configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_collection_service().add(
            name, audit_id
        )

    except Exception as exc:
        _render_collection_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "status": "added",
                "collection": name,
                "audit_id": audit_id,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Audit '{audit_id}' added to collection '{name}'.\n"
        )

    return 0


def run_deployment_governance_audit_collection_remove(
    *,
    name: str,
    audit_id: str,
    json_output: bool = False,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """
    Bootstrap persistence and remove an audit from a collection.

    Exit codes: 0 the audit was removed, 2 the audit could not be
    removed (not a member, or invalid configuration).
    """

    try:
        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_audit_collection_service().remove(
            name, audit_id
        )

    except Exception as exc:
        _render_collection_failure(
            exc, json_output=json_output, stderr=stderr
        )

        return 2

    if json_output:
        json.dump(
            {
                "status": "removed",
                "collection": name,
                "audit_id": audit_id,
            },
            stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        stdout.write("\n")

    else:
        stdout.write(
            f"Audit '{audit_id}' removed from collection '{name}'.\n"
        )

    return 0


def _render_collection_json(
    collection: GovernanceIntegrityAuditCollection,
    *,
    audits: tuple[str, ...],
    stdout: TextIO,
) -> None:
    payload = collection.to_dict()

    payload["audits"] = list(audits)

    json.dump(
        payload,
        stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    stdout.write("\n")


def _render_collection_failure(
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
        "Governance audit collection operation could not be "
        "completed.\n"
    )

    stderr.write(f"Reason: {error}\n")
