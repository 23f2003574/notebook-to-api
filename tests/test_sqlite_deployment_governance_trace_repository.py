from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.observability.deployment_governance_trace_repository import (
    GovernanceTraceRecord,
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
        == 2
    )

    applied_versions = tuple(
        migration.version
        for migration in database.applied_migrations()
    )

    assert applied_versions == (
        1,
        2,
    )
