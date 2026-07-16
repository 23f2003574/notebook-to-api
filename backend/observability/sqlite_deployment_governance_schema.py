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

DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE: Final[
    str
] = "deployment_governance_integrity_audits"

DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE: Final[
    str
] = "audit_bookmarks"

DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE: Final[
    str
] = "audit_labels"


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
            DeploymentGovernanceSQLiteSchema._add_integrity_metadata_migration(),
            DeploymentGovernanceSQLiteSchema._create_integrity_audit_history_migration(),
            DeploymentGovernanceSQLiteSchema._create_audit_bookmarks_migration(),
            DeploymentGovernanceSQLiteSchema._create_audit_labels_migration(),
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

    @staticmethod
    def _add_integrity_metadata_migration() -> SQLiteMigration:
        """
        Migration 3 adds integrity metadata columns for detecting
        corruption or unexpected mutation of persisted governance traces.

        The columns are nullable because rows already persisted under
        schema version 2 have no historical digest to backfill; they are
        recognized as unverified legacy data rather than given a fabricated
        digest.
        """

        return SQLiteMigration(
            version=3,
            name="add deployment governance trace integrity metadata",
            statements=(
                f"""
                ALTER TABLE
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                ADD COLUMN
                    integrity_algorithm TEXT
                """,
                f"""
                ALTER TABLE
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                ADD COLUMN
                    integrity_version INTEGER
                """,
                f"""
                ALTER TABLE
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                ADD COLUMN
                    integrity_digest TEXT
                """,
            ),
        )

    @staticmethod
    def _create_integrity_audit_history_migration() -> SQLiteMigration:
        """
        Migration 4 creates durable storage for completed governance
        integrity audits (backend/observability/deployment_governance_audit_history.py),
        recording one compact aggregate summary per audit run rather than
        every individual finding.
        """

        return SQLiteMigration(
            version=4,
            name="create governance integrity audit history table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                (
                    audit_id TEXT NOT NULL
                        PRIMARY KEY,

                    backend TEXT NOT NULL,

                    started_at TEXT NOT NULL,

                    completed_at TEXT NOT NULL,

                    outcome TEXT NOT NULL,

                    total_records INTEGER NOT NULL,

                    valid_records INTEGER NOT NULL,

                    invalid_records INTEGER NOT NULL,

                    integrity_mismatches INTEGER NOT NULL,

                    missing_integrity_metadata INTEGER NOT NULL,

                    invalid_integrity_metadata INTEGER NOT NULL,

                    invalid_persisted_records INTEGER NOT NULL,

                    CHECK (
                        length(
                            trim(audit_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(backend)
                        ) > 0
                    ),

                    CHECK (
                        outcome IN (
                            'healthy',
                            'unhealthy'
                        )
                    ),

                    CHECK (
                        completed_at >= started_at
                    ),

                    CHECK (total_records >= 0),
                    CHECK (valid_records >= 0),
                    CHECK (invalid_records >= 0),
                    CHECK (integrity_mismatches >= 0),
                    CHECK (missing_integrity_metadata >= 0),
                    CHECK (invalid_integrity_metadata >= 0),
                    CHECK (invalid_persisted_records >= 0),

                    CHECK (
                        valid_records + invalid_records
                        = total_records
                    ),

                    CHECK (
                        integrity_mismatches
                        + missing_integrity_metadata
                        + invalid_integrity_metadata
                        + invalid_persisted_records
                        = invalid_records
                    )
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_integrity_audits_started_at
                ON {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                (
                    started_at DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_integrity_audits_outcome_started_at
                ON {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                (
                    outcome,
                    started_at DESC
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_integrity_audits_backend_started_at
                ON {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                (
                    backend,
                    started_at DESC
                )
                """,
            ),
        )

    @staticmethod
    def _create_audit_bookmarks_migration() -> SQLiteMigration:
        """
        Migration 5 creates storage for named bookmarks pointing at
        recorded governance integrity audits
        (backend/observability/deployment_governance_audit_bookmarks.py).

        Bookmarks are separate metadata layered on top of audit history
        for quick navigation; this table does not reference the audit
        history table via a foreign key so a bookmark can still be
        inspected even if retention later prunes the audit it points to.
        """

        return SQLiteMigration(
            version=5,
            name="create governance audit bookmarks table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    audit_id TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(audit_id)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_audit_labels_migration() -> SQLiteMigration:
        """
        Migration 6 creates storage for many-to-many labels applied to
        recorded governance integrity audits
        (backend/observability/deployment_governance_audit_labels.py).

        Unlike a bookmark (one unique name per audit), the same label may
        be applied to many audits and the same audit may carry many
        labels, so the primary key is the (audit_id, label) pair rather
        than a single column.
        """

        return SQLiteMigration(
            version=6,
            name="create governance audit labels table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                (
                    audit_id TEXT NOT NULL,

                    label TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    PRIMARY KEY (
                        audit_id,
                        label
                    ),

                    CHECK (
                        length(
                            trim(audit_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(label)
                        ) > 0
                    )
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_audit_labels_label
                ON {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                (
                    label,
                    created_at DESC
                )
                """,
            ),
        )
