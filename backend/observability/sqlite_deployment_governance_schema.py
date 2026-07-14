from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteMigration,
)


DEPLOYMENT_GOVERNANCE_TRACE_TABLE: Final[
    str
] = "deployment_governance_traces"


@dataclass(frozen=True)
class DeploymentGovernanceSQLiteSchema:
    """
    Versioned SQLite schema definition for deployment governance persistence.

    The schema stores queryable governance metadata in dedicated relational
    columns while preserving the complete serialized lifecycle in a JSON
    payload column.

    This gives the persistence layer:

    - efficient indexed queries,
    - complete domain reconstruction,
    - schema migration support,
    - storage independence above the repository boundary.
    """

    @staticmethod
    def migrations() -> tuple[SQLiteMigration, ...]:
        """
        Return all deployment governance schema migrations in version order.
        """

        return (
            DeploymentGovernanceSQLiteSchema._create_trace_table_migration(),
            DeploymentGovernanceSQLiteSchema._create_query_indexes_migration(),
        )

    @staticmethod
    def initialize(
        database: SQLiteDatabase,
    ) -> tuple[int, ...]:
        """
        Initialize the database and apply all deployment governance migrations.

        Returns the migration versions applied during this invocation.
        """

        database.initialize()

        return database.apply_migrations(
            DeploymentGovernanceSQLiteSchema.migrations()
        )

    @staticmethod
    def _create_trace_table_migration() -> SQLiteMigration:
        """
        Migration 1 creates the canonical deployment governance trace table.
        """

        return SQLiteMigration(
            version=1,
            name="create deployment governance trace table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    trace_id TEXT NOT NULL
                        PRIMARY KEY,

                    deployment_id TEXT NOT NULL
                        UNIQUE,

                    service_name TEXT NOT NULL,

                    environment TEXT NOT NULL,

                    artifact_digest TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    updated_at TEXT NOT NULL,

                    governance_state TEXT NOT NULL,

                    final_status TEXT NULL,

                    completed INTEGER NOT NULL
                        CHECK (
                            completed IN (0, 1)
                        ),

                    payload TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(trace_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(deployment_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(service_name)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(environment)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(artifact_digest)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(governance_state)
                        ) > 0
                    ),

                    CHECK (
                        updated_at >= created_at
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_query_indexes_migration() -> SQLiteMigration:
        """
        Migration 2 creates indexes for the repository's primary query paths.

        The index set is deliberately aligned with GovernanceTraceQuery and
        expected dashboard/API access patterns rather than indexing every
        column indiscriminately.
        """

        return SQLiteMigration(
            version=2,
            name="create deployment governance trace query indexes",
            statements=(
                f"""
                CREATE INDEX
                idx_governance_traces_created_at
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_service_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    service_name,
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_environment_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    environment,
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_state_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    governance_state,
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_status_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    final_status,
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_completed_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    completed,
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_service_environment_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    service_name,
                    environment,
                    created_at DESC,
                    trace_id DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_traces_environment_state_created
                ON {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                (
                    environment,
                    governance_state,
                    created_at DESC,
                    trace_id DESC
                )
                """,
            ),
        )
