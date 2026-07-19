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

DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE: Final[
    str
] = "saved_audit_queries"

DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE: Final[
    str
] = "audit_collections"

DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE: Final[
    str
] = "audit_collection_entries"

DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE: Final[
    str
] = "audit_report_templates"

DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE: Final[
    str
] = "audit_report_schedules"

DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE: Final[
    str
] = "failure_policies"

DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE: Final[
    str
] = "notifications"

DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE: Final[
    str
] = "notification_channels"

DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE: Final[
    str
] = "notification_dispatches"

DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE: Final[
    str
] = "delivery_history"

DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE: Final[
    str
] = "notification_preferences"

DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE: Final[
    str
] = "delivery_policies"

DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE: Final[
    str
] = "provider_configurations"

DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE: Final[
    str
] = "provider_secrets"

DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE: Final[
    str
] = "scheduled_dispatches"

DEPLOYMENT_GOVERNANCE_METRICS_TABLE: Final[
    str
] = "governance_metrics"

DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE: Final[
    str
] = "governance_metric_history"


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
            DeploymentGovernanceSQLiteSchema._create_saved_audit_queries_migration(),
            DeploymentGovernanceSQLiteSchema._create_audit_collections_migration(),
            DeploymentGovernanceSQLiteSchema._create_audit_report_templates_migration(),
            DeploymentGovernanceSQLiteSchema._create_audit_report_schedules_migration(),
            DeploymentGovernanceSQLiteSchema._create_failure_policies_migration(),
            DeploymentGovernanceSQLiteSchema._create_notifications_migration(),
            DeploymentGovernanceSQLiteSchema._create_notification_channels_migration(),
            DeploymentGovernanceSQLiteSchema._create_notification_dispatches_migration(),
            DeploymentGovernanceSQLiteSchema._create_delivery_history_migration(),
            DeploymentGovernanceSQLiteSchema._create_notification_preferences_migration(),
            DeploymentGovernanceSQLiteSchema._create_delivery_policies_migration(),
            DeploymentGovernanceSQLiteSchema._create_provider_configurations_migration(),
            DeploymentGovernanceSQLiteSchema._create_provider_secrets_migration(),
            DeploymentGovernanceSQLiteSchema._create_scheduled_dispatches_migration(),
            DeploymentGovernanceSQLiteSchema._create_governance_metrics_migration(),
            DeploymentGovernanceSQLiteSchema._create_governance_metrics_history_migration(),
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

    @staticmethod
    def _create_saved_audit_queries_migration() -> SQLiteMigration:
        """
        Migration 7 creates storage for named, reusable governance audit
        search filters
        (backend/observability/deployment_governance_audit_saved_queries.py).

        The filter criteria (GovernanceIntegrityAuditSearchQuery) are
        stored as a JSON payload in query_json rather than individual
        columns, since the query shape is owned by the search module,
        not this schema.
        """

        return SQLiteMigration(
            version=7,
            name="create saved governance audit queries table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    query_json TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(query_json)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_audit_collections_migration() -> SQLiteMigration:
        """
        Migration 8 creates storage for named, explicit groups of
        governance integrity audits and their membership
        (backend/observability/deployment_governance_audit_collections.py).

        Unlike a saved query (reusable filter criteria, re-evaluated on
        every run), a collection stores explicit membership decided by
        the operator. Membership rows live in a separate entries table
        rather than a foreign key with ON DELETE CASCADE, so that
        cascade behavior (deleting a collection removes its entries)
        stays owned by the repository/service layer rather than the
        database engine.
        """

        return SQLiteMigration(
            version=8,
            name="create governance audit collections tables",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    description TEXT,

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    )
                )
                """,

                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                (
                    collection TEXT NOT NULL,

                    audit_id TEXT NOT NULL,

                    added_at TEXT NOT NULL,

                    PRIMARY KEY (
                        collection,
                        audit_id
                    ),

                    CHECK (
                        length(
                            trim(collection)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(audit_id)
                        ) > 0
                    )
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_audit_collection_entries_audit_id
                ON {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                (
                    audit_id,
                    added_at DESC
                )
                """,
            ),
        )

    @staticmethod
    def _create_audit_report_templates_migration() -> SQLiteMigration:
        """
        Migration 9 creates storage for named, reusable governance audit
        report configurations
        (backend/observability/deployment_governance_audit_report_templates.py).

        A template references a collection or saved query by name
        rather than embedding its own copy of the selection criteria,
        so a template always reflects that source's current state when
        generated.
        """

        return SQLiteMigration(
            version=9,
            name="create governance audit report templates table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    title TEXT NOT NULL,

                    source TEXT NOT NULL,

                    source_name TEXT NOT NULL,

                    output_format TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(title)
                        ) > 0
                    ),

                    CHECK (
                        source IN (
                            'collection',
                            'saved_query'
                        )
                    ),

                    CHECK (
                        length(
                            trim(source_name)
                        ) > 0
                    ),

                    CHECK (
                        output_format IN (
                            'json',
                            'markdown'
                        )
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_audit_report_schedules_migration() -> SQLiteMigration:
        """
        Migration 10 creates storage for named execution plans for
        report templates
        (backend/observability/deployment_governance_audit_report_schedule.py).

        This layer only manages schedule and execution metadata; no
        background worker consumes it yet.
        """

        return SQLiteMigration(
            version=10,
            name="create governance audit report schedules table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    template_name TEXT NOT NULL,

                    frequency TEXT NOT NULL,

                    enabled INTEGER NOT NULL
                        CHECK (
                            enabled IN (0, 1)
                        ),

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(template_name)
                        ) > 0
                    ),

                    CHECK (
                        frequency IN (
                            'daily',
                            'weekly',
                            'monthly'
                        )
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_failure_policies_migration() -> SQLiteMigration:
        """
        Migration 11 creates storage for named governance audit
        failure policies
        (backend/observability/deployment_governance_failure_policy.py).

        A policy only records the configured action and retry budget;
        it does not reference any particular execution job, so it has
        no foreign key relationship to the (in-memory only) execution
        or retry tables.
        """

        return SQLiteMigration(
            version=11,
            name="create governance audit failure policies table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    action TEXT NOT NULL,

                    max_retry_attempts INTEGER NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        action IN (
                            'ignore',
                            'retry',
                            'dead_letter'
                        )
                    ),

                    CHECK (max_retry_attempts >= 0)
                )
                """,
            ),
        )

    @staticmethod
    def _create_notifications_migration() -> SQLiteMigration:
        """
        Migration 12 creates storage for queued governance audit
        notifications
        (backend/observability/deployment_governance_notifications.py).

        A notification records the alert it was created from
        (alert_id) so the notification pipeline can recognize and
        skip an alert it has already queued a notification for, but
        it has no foreign key relationship to any alert store since
        alerts are generated on demand and never persisted themselves.
        """

        return SQLiteMigration(
            version=12,
            name="create governance audit notifications table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                (
                    notification_id TEXT NOT NULL
                        PRIMARY KEY,

                    alert_id TEXT NOT NULL,

                    severity TEXT NOT NULL,

                    message TEXT NOT NULL,

                    status TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(notification_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(alert_id)
                        ) > 0
                    ),

                    CHECK (
                        severity IN (
                            'info',
                            'warning',
                            'critical'
                        )
                    ),

                    CHECK (
                        length(
                            trim(message)
                        ) > 0
                    ),

                    CHECK (
                        status IN (
                            'pending'
                        )
                    )
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_notifications_alert_id
                ON {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                (
                    alert_id
                )
                """,
            ),
        )

    @staticmethod
    def _create_notification_channels_migration() -> SQLiteMigration:
        """
        Migration 13 creates storage for named governance audit
        notification delivery channels
        (backend/observability/deployment_governance_notification_channels.py).

        No delivery happens yet: a channel only records where a
        future provider would send a notification, not how it is
        sent.
        """

        return SQLiteMigration(
            version=13,
            name="create governance audit notification channels table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    channel_type TEXT NOT NULL,

                    destination TEXT NOT NULL,

                    enabled INTEGER NOT NULL
                        CHECK (
                            enabled IN (0, 1)
                        ),

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        channel_type IN (
                            'email',
                            'webhook',
                            'slack'
                        )
                    ),

                    CHECK (
                        length(
                            trim(destination)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_notification_dispatches_migration() -> SQLiteMigration:
        """
        Migration 14 creates storage for governance audit notification
        dispatch records
        (backend/observability/deployment_governance_notification_dispatcher.py),
        recording one delivery attempt per (notification, channel)
        pair the dispatcher has matched.

        No external delivery happens yet: a dispatch only records that
        a notification was matched to a channel, not that it was
        actually sent.
        """

        return SQLiteMigration(
            version=14,
            name="create governance audit notification dispatches table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                (
                    dispatch_id TEXT NOT NULL
                        PRIMARY KEY,

                    notification_id TEXT NOT NULL,

                    channel_name TEXT NOT NULL,

                    status TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(dispatch_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(notification_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(channel_name)
                        ) > 0
                    ),

                    CHECK (
                        status IN (
                            'queued'
                        )
                    )
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_notification_dispatches_pair
                ON {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                (
                    notification_id,
                    channel_name
                )
                """,
            ),
        )

    @staticmethod
    def _create_delivery_history_migration() -> SQLiteMigration:
        """
        Migration 15 creates immutable storage for governance audit
        notification delivery outcomes
        (backend/observability/deployment_governance_delivery_history.py),
        recording one permanent record per delivered dispatch for
        auditing.
        """

        return SQLiteMigration(
            version=15,
            name="create governance audit delivery history table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE}
                (
                    delivery_id TEXT NOT NULL
                        PRIMARY KEY,

                    dispatch_id TEXT NOT NULL,

                    channel_name TEXT NOT NULL,

                    status TEXT NOT NULL,

                    delivered_at TEXT NOT NULL,

                    error TEXT,

                    CHECK (
                        length(
                            trim(delivery_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(dispatch_id)
                        ) > 0
                    ),

                    CHECK (
                        length(
                            trim(channel_name)
                        ) > 0
                    ),

                    CHECK (
                        status IN (
                            'success',
                            'failed'
                        )
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_notification_preferences_migration() -> SQLiteMigration:
        """
        Migration 16 creates storage for named governance audit
        notification routing preferences
        (backend/observability/deployment_governance_notification_preferences.py).

        The channel list is stored as a JSON array in the channels
        column rather than a separate join table, since preferences
        own their channel list outright (no independent lifecycle for
        a preference/channel pairing).
        """

        return SQLiteMigration(
            version=16,
            name="create governance audit notification preferences table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE}
                (
                    name TEXT NOT NULL
                        PRIMARY KEY,

                    minimum_severity TEXT NOT NULL,

                    channels TEXT NOT NULL,

                    enabled INTEGER NOT NULL
                        CHECK (
                            enabled IN (0, 1)
                        ),

                    created_at TEXT NOT NULL,

                    CHECK (
                        length(
                            trim(name)
                        ) > 0
                    ),

                    CHECK (
                        minimum_severity IN (
                            'info',
                            'warning',
                            'critical'
                        )
                    ),

                    CHECK (
                        length(
                            trim(channels)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_delivery_policies_migration() -> SQLiteMigration:
        """
        Migration 17 creates storage for per-channel governance audit
        delivery policies
        (backend/observability/deployment_governance_delivery_policies.py):
        retry, timeout, and rate-limit configuration exposed to
        delivery providers.

        This commit configures delivery behavior only; current stub
        providers may ignore these values.
        """

        return SQLiteMigration(
            version=17,
            name="create governance audit delivery policies table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE}
                (
                    channel_name TEXT NOT NULL
                        PRIMARY KEY,

                    retry_limit INTEGER NOT NULL
                        CHECK (retry_limit >= 0),

                    timeout_seconds INTEGER NOT NULL
                        CHECK (timeout_seconds > 0),

                    rate_limit_per_minute INTEGER NOT NULL
                        CHECK (rate_limit_per_minute > 0),

                    enabled INTEGER NOT NULL
                        CHECK (
                            enabled IN (0, 1)
                        ),

                    CHECK (
                        length(
                            trim(channel_name)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_provider_configurations_migration() -> SQLiteMigration:
        """
        Migration 18 creates storage for typed runtime settings of
        governance audit delivery providers
        (backend/observability/deployment_governance_provider_configuration.py).

        The settings themselves are stored as a JSON object in
        configuration_json rather than individual columns, since each
        provider defines its own configuration keys.
        """

        return SQLiteMigration(
            version=18,
            name="create governance audit provider configurations table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE}
                (
                    channel_type TEXT NOT NULL
                        PRIMARY KEY,

                    configuration_json TEXT NOT NULL,

                    updated_at TEXT NOT NULL,

                    CHECK (
                        channel_type IN (
                            'email',
                            'webhook',
                            'slack'
                        )
                    ),

                    CHECK (
                        length(
                            trim(configuration_json)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_provider_secrets_migration() -> SQLiteMigration:
        """
        Migration 19 creates storage for sensitive credentials of
        governance audit delivery providers
        (backend/observability/deployment_governance_provider_secrets.py),
        kept in a table separate from provider_configurations so
        secrets and non-sensitive settings have independent
        lifecycles.

        The values themselves are stored as a JSON object in
        secrets_json rather than individual columns, since each
        provider defines its own secret keys. This is local,
        unencrypted storage.
        """

        return SQLiteMigration(
            version=19,
            name="create governance audit provider secrets table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE}
                (
                    channel_type TEXT NOT NULL
                        PRIMARY KEY,

                    secrets_json TEXT NOT NULL,

                    updated_at TEXT NOT NULL,

                    CHECK (
                        channel_type IN (
                            'email',
                            'webhook',
                            'slack'
                        )
                    ),

                    CHECK (
                        length(
                            trim(secrets_json)
                        ) > 0
                    )
                )
                """,
            ),
        )

    @staticmethod
    def _create_scheduled_dispatches_migration() -> SQLiteMigration:
        """
        Migration 20 creates storage for the governance audit
        delivery scheduler's unified queue
        (backend/observability/deployment_governance_delivery_scheduler.py).
        """

        return SQLiteMigration(
            version=20,
            name="create governance audit scheduled dispatches table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                (
                    dispatch_id TEXT NOT NULL
                        PRIMARY KEY,

                    scheduled_at TEXT NOT NULL,

                    state TEXT NOT NULL,

                    attempt INTEGER NOT NULL
                        CHECK (attempt >= 0),

                    CHECK (
                        state IN (
                            'pending',
                            'ready',
                            'running',
                            'completed',
                            'cancelled'
                        )
                    )
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_scheduled_dispatches_state_scheduled_at
                ON {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                (
                    state,
                    scheduled_at
                )
                """,
            ),
        )

    @staticmethod
    def _create_governance_metrics_migration() -> SQLiteMigration:
        """
        Migration 21 creates storage for the single persisted
        snapshot of live governance audit notification delivery
        metrics
        (backend/observability/deployment_governance_metrics.py).

        The row is pinned to id = 1 so the table can only ever hold
        one snapshot: metrics are a running singleton, not a history.
        """

        return SQLiteMigration(
            version=21,
            name="create governance metrics table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_METRICS_TABLE}
                (
                    id INTEGER NOT NULL
                        PRIMARY KEY,

                    metrics_json TEXT NOT NULL,

                    updated_at TEXT NOT NULL,

                    CHECK (id = 1)
                )
                """,
            ),
        )

    @staticmethod
    def _create_governance_metrics_history_migration() -> (
        SQLiteMigration
    ):
        """
        Migration 22 creates append-only storage for periodic
        immutable governance metrics snapshots, used for trend
        analysis
        (backend/observability/deployment_governance_metrics_history.py).

        Unlike governance_metrics, which holds the single current
        counters, this table accumulates one row per captured
        snapshot and is never updated in place, only appended to and
        pruned.
        """

        return SQLiteMigration(
            version=22,
            name="create governance metric history table",
            statements=(
                f"""
                CREATE TABLE
                {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
                (
                    id INTEGER
                        PRIMARY KEY,

                    captured_at TEXT NOT NULL,

                    metrics_json TEXT NOT NULL
                )
                """,

                f"""
                CREATE INDEX
                idx_governance_metric_history_captured_at
                ON {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
                (
                    captured_at
                )
                """,
            ),
        )
