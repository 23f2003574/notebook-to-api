from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.observability.deployment_governance_trace_integrity import (
    GovernanceTraceIntegrityMismatchError,
)
from backend.observability.deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)
from backend.observability.sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_TRACE_TABLE,
)
from backend.observability.sqlite_deployment_governance_trace_repository import (
    SQLiteDeploymentGovernanceTraceRepository,
)
from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)


def test_sqlite_repository_survives_reconstruction(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "durable-governance.db"
    )

    first_database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=database_path,
        )
    )

    first_repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            first_database
        )
    )

    timestamp = datetime(
        2026,
        7,
        14,
        10,
        0,
        0,
        tzinfo=timezone.utc,
    )

    record = GovernanceTraceRecord(
        trace_id="trace-durable",
        deployment_id="deployment-durable",
        service_name="payments-api",
        environment="production",
        artifact_digest="sha256:durable",
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "trace": {
                "trace_id": "trace-durable",
            },
            "events": [],
        },
    )

    first_repository.save(
        record
    )

    del first_repository
    del first_database

    second_database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=database_path,
        )
    )

    second_repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            second_database
        )
    )

    restored = (
        second_repository.get_by_trace_id(
            "trace-durable"
        )
    )

    assert restored == record


def test_sqlite_repository_schema_initialization_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "schema-idempotency.db"
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=database_path,
        )
    )

    SQLiteDeploymentGovernanceTraceRepository(
        database
    )

    SQLiteDeploymentGovernanceTraceRepository(
        database
    )

    assert (
        database.current_schema_version()
        == 12
    )

    applied_versions = tuple(
        migration.version
        for migration in database.applied_migrations()
    )

    assert applied_versions == (
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
    )


def test_sqlite_repository_detects_tampered_metadata(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=(
                tmp_path
                / "tampered-metadata.db"
            ),
        )
    )

    repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            database
        )
    )

    timestamp = datetime(
        2026,
        7,
        14,
        12,
        30,
        0,
        tzinfo=timezone.utc,
    )

    record = GovernanceTraceRecord(
        trace_id="trace-tampered-metadata",
        deployment_id="deployment-tampered-metadata",
        service_name="payments-api",
        environment="production",
        artifact_digest="sha256:tampered",
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "events": [],
        },
    )

    repository.save(
        record
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            f"""
            UPDATE
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
            SET
                environment = ?
            WHERE
                trace_id = ?
            """,
            (
                "staging",
                record.trace_id,
            ),
        )

    with pytest.raises(
        GovernanceTraceIntegrityMismatchError
    ):
        repository.get_by_trace_id(
            record.trace_id
        )


def test_sqlite_repository_detects_tampered_payload(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=(
                tmp_path
                / "tampered-payload.db"
            ),
        )
    )

    repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            database
        )
    )

    timestamp = datetime(
        2026,
        7,
        14,
        13,
        0,
        0,
        tzinfo=timezone.utc,
    )

    record = GovernanceTraceRecord(
        trace_id="trace-tampered-payload",
        deployment_id="deployment-tampered-payload",
        service_name="payments-api",
        environment="production",
        artifact_digest="sha256:payload",
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "events": [],
        },
    )

    repository.save(
        record
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            f"""
            UPDATE
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
            SET
                payload = ?
            WHERE
                trace_id = ?
            """,
            (
                '{"schema_version":1,"events":["tampered"]}',
                record.trace_id,
            ),
        )

    with pytest.raises(
        GovernanceTraceIntegrityMismatchError
    ):
        repository.get_by_trace_id(
            record.trace_id
        )


def test_sqlite_repository_update_refreshes_integrity_metadata(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=(
                tmp_path
                / "integrity-update.db"
            ),
        )
    )

    repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            database
        )
    )

    timestamp = datetime(
        2026,
        7,
        14,
        13,
        30,
        0,
        tzinfo=timezone.utc,
    )

    original = GovernanceTraceRecord(
        trace_id="trace-integrity-update",
        deployment_id="deployment-integrity-update",
        service_name="payments-api",
        environment="production",
        artifact_digest="sha256:update",
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "events": [],
        },
    )

    repository.save(
        original
    )

    updated = GovernanceTraceRecord(
        trace_id=original.trace_id,
        deployment_id=original.deployment_id,
        service_name=original.service_name,
        environment=original.environment,
        artifact_digest=original.artifact_digest,
        created_at=original.created_at,
        updated_at=original.updated_at,
        governance_state="succeeded",
        final_status="succeeded",
        completed=True,
        payload={
            "schema_version": 1,
            "events": [
                {
                    "type": "deployment_succeeded",
                }
            ],
        },
    )

    repository.update(
        updated
    )

    restored = repository.get_by_trace_id(
        updated.trace_id
    )

    assert restored == updated


def test_sqlite_repository_legacy_row_without_integrity_metadata_is_rejected(
    tmp_path: Path,
) -> None:
    """
    Rows written before Commit #10 (schema version 2) have NULL integrity
    columns after migrating to version 3. Reading such a row must raise a
    distinct "missing metadata" error rather than a mismatch error, or
    silently accepting unverified legacy data.
    """

    from backend.observability.deployment_governance_trace_integrity import (
        GovernanceTraceIntegrityMetadataMissingError,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=(
                tmp_path
                / "legacy-row.db"
            ),
        )
    )

    repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            database
        )
    )

    timestamp = datetime(
        2026,
        7,
        14,
        14,
        0,
        0,
        tzinfo=timezone.utc,
    )

    record = GovernanceTraceRecord(
        trace_id="trace-legacy",
        deployment_id="deployment-legacy",
        service_name="payments-api",
        environment="production",
        artifact_digest="sha256:legacy",
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "events": [],
        },
    )

    repository.save(
        record
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            f"""
            UPDATE
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
            SET
                integrity_algorithm = NULL,
                integrity_version = NULL,
                integrity_digest = NULL
            WHERE
                trace_id = ?
            """,
            (
                record.trace_id,
            ),
        )

    with pytest.raises(
        GovernanceTraceIntegrityMetadataMissingError
    ):
        repository.get_by_trace_id(
            record.trace_id
        )
