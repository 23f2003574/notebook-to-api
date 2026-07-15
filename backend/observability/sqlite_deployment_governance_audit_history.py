from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_history import (
    GovernanceIntegrityAuditAlreadyExistsError,
    GovernanceIntegrityAuditHistoryQuery,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityAuditHistoryRepository:
    """
    Durable SQLite implementation of governance integrity audit history.

    Conforms structurally to GovernanceIntegrityAuditHistoryRepository so
    callers can swap this repository for
    InMemoryGovernanceIntegrityAuditHistoryRepository without observing
    different behavior. Historical audit records are append-only: this
    repository intentionally exposes no update or upsert operation.
    """

    _SELECT_COLUMNS = """
        audit_id,
        backend,
        started_at,
        completed_at,
        outcome,
        total_records,
        valid_records,
        invalid_records,
        integrity_mismatches,
        missing_integrity_metadata,
        invalid_integrity_metadata,
        invalid_persisted_records
    """

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._database = database

        if initialize_schema:
            DeploymentGovernanceSQLiteSchema.initialize(
                self._database
            )

    @property
    def database(self) -> SQLiteDatabase:
        return self._database

    def save(
        self,
        record: GovernanceIntegrityAuditRecord,
    ) -> GovernanceIntegrityAuditRecord:
        """
        Persist one immutable completed integrity audit.
        """

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                    (
                        audit_id,
                        backend,
                        started_at,
                        completed_at,
                        outcome,
                        total_records,
                        valid_records,
                        invalid_records,
                        integrity_mismatches,
                        missing_integrity_metadata,
                        invalid_integrity_metadata,
                        invalid_persisted_records
                    )
                    VALUES
                    (
                        :audit_id,
                        :backend,
                        :started_at,
                        :completed_at,
                        :outcome,
                        :total_records,
                        :valid_records,
                        :invalid_records,
                        :integrity_mismatches,
                        :missing_integrity_metadata,
                        :invalid_integrity_metadata,
                        :invalid_persisted_records
                    )
                    """,
                    self._record_to_parameters(record),
                )

        except sqlite3.IntegrityError as exc:
            raise GovernanceIntegrityAuditAlreadyExistsError(
                "governance integrity audit "
                f"'{record.audit_id}' already exists"
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance integrity audit "
                f"'{record.audit_id}'"
            ) from exc

        return record

    def get_by_audit_id(
        self,
        audit_id: str,
    ) -> GovernanceIntegrityAuditRecord | None:
        normalized_audit_id = audit_id.strip()

        if not normalized_audit_id:
            raise ValueError(
                "audit_id must not be empty"
            )

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                WHERE
                    audit_id = ?
                """,
                (normalized_audit_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance integrity audit "
                f"'{normalized_audit_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def latest(
        self,
    ) -> GovernanceIntegrityAuditRecord | None:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                ORDER BY
                    started_at DESC,
                    audit_id DESC
                LIMIT 1
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve the latest governance integrity audit"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def list(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[GovernanceIntegrityAuditRecord, ...]:
        if limit is not None and limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        sql = f"""
            SELECT
                {self._SELECT_COLUMNS}
            FROM
                {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
            ORDER BY
                started_at DESC,
                audit_id DESC
        """

        parameters: list[Any] = []

        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)

        try:
            rows = self._database.query_all(sql, parameters)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance integrity audits"
            ) from exc

        return tuple(self._row_to_record(row) for row in rows)

    def query(
        self,
        query: GovernanceIntegrityAuditHistoryQuery,
    ) -> tuple[GovernanceIntegrityAuditRecord, ...]:
        where_clauses, parameters = self._build_filters(query)

        sql = f"""
            SELECT
                {self._SELECT_COLUMNS}
            FROM
                {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
        """

        if where_clauses:
            sql += "\nWHERE\n    " + "\n    AND ".join(where_clauses)

        sql += """
            ORDER BY
                started_at DESC,
                audit_id DESC
        """

        if query.limit is not None:
            sql += " LIMIT ?"
            parameters.append(query.limit)

        try:
            rows = self._database.query_all(sql, parameters)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to query governance integrity audits"
            ) from exc

        return tuple(self._row_to_record(row) for row in rows)

    def count(
        self,
    ) -> int:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    COUNT(*) AS count
                FROM
                    {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to count governance integrity audits"
            ) from exc

        return 0 if row is None else int(row["count"])

    def count_by_outcome(
        self,
        outcome: GovernanceIntegrityAuditOutcome,
    ) -> int:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    COUNT(*) AS count
                FROM
                    {DEPLOYMENT_GOVERNANCE_INTEGRITY_AUDIT_TABLE}
                WHERE
                    outcome = ?
                """,
                (outcome.value,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to count governance integrity audits by outcome"
            ) from exc

        return 0 if row is None else int(row["count"])

    def _build_filters(
        self,
        query: GovernanceIntegrityAuditHistoryQuery,
    ) -> tuple[list[str], list[Any]]:
        where_clauses: list[str] = []
        parameters: list[Any] = []

        if query.backend is not None:
            where_clauses.append("backend = ?")
            parameters.append(query.backend)

        if query.outcome is not None:
            where_clauses.append("outcome = ?")
            parameters.append(query.outcome.value)

        if query.started_at_or_after is not None:
            where_clauses.append("started_at >= ?")
            parameters.append(
                self._datetime_to_storage(query.started_at_or_after)
            )

        if query.started_at_or_before is not None:
            where_clauses.append("started_at <= ?")
            parameters.append(
                self._datetime_to_storage(query.started_at_or_before)
            )

        return where_clauses, parameters

    @classmethod
    def _record_to_parameters(
        cls,
        record: GovernanceIntegrityAuditRecord,
    ) -> dict[str, Any]:
        return {
            "audit_id": record.audit_id,
            "backend": record.backend,
            "started_at": cls._datetime_to_storage(record.started_at),
            "completed_at": cls._datetime_to_storage(record.completed_at),
            "outcome": record.outcome.value,
            "total_records": record.total_records,
            "valid_records": record.valid_records,
            "invalid_records": record.invalid_records,
            "integrity_mismatches": record.integrity_mismatches,
            "missing_integrity_metadata": record.missing_integrity_metadata,
            "invalid_integrity_metadata": record.invalid_integrity_metadata,
            "invalid_persisted_records": record.invalid_persisted_records,
        }

    @classmethod
    def _row_to_record(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityAuditRecord:
        return GovernanceIntegrityAuditRecord(
            audit_id=str(row["audit_id"]),
            backend=str(row["backend"]),
            started_at=cls._datetime_from_storage(str(row["started_at"])),
            completed_at=cls._datetime_from_storage(str(row["completed_at"])),
            outcome=GovernanceIntegrityAuditOutcome(row["outcome"]),
            total_records=int(row["total_records"]),
            valid_records=int(row["valid_records"]),
            invalid_records=int(row["invalid_records"]),
            integrity_mismatches=int(row["integrity_mismatches"]),
            missing_integrity_metadata=int(row["missing_integrity_metadata"]),
            invalid_integrity_metadata=int(row["invalid_integrity_metadata"]),
            invalid_persisted_records=int(row["invalid_persisted_records"]),
        )

    @staticmethod
    def _datetime_to_storage(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        return value.isoformat()

    @staticmethod
    def _datetime_from_storage(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)
