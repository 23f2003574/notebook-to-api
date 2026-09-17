from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_trace_repository import (
    DeploymentGovernanceTraceRepository,
    GovernanceTraceQuery,
    GovernanceTraceRecord,
    GovernanceTraceRepositoryStatistics,
)
from .deployment_governance_integrity_audit import (
    GovernanceTraceIntegrityAuditCandidate,
)
from .deployment_governance_trace_integrity import (
    DeploymentGovernanceTraceIntegrity,
    GovernanceTraceIntegrityMetadata,
    GovernanceTraceIntegrityMetadataMissingError,
)
from .in_memory_deployment_governance_trace_repository import (
    GovernanceTraceAlreadyExistsError,
    GovernanceTraceNotFoundError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_TRACE_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceTraceSerializationError(SQLitePersistenceError):
    """
    Raised when a governance trace record cannot be serialized to or restored
    from its SQLite representation.
    """


class SQLiteDeploymentGovernanceTraceRepository(
    DeploymentGovernanceTraceRepository
):
    """
    Durable SQLite implementation of DeploymentGovernanceTraceRepository.

    The repository persists storage-neutral GovernanceTraceRecord instances
    using the versioned deployment governance SQLite schema, and conforms to
    the exact error types and statistics shape established by the abstract
    repository contract (deployment_governance_trace_repository.py) and its
    in-memory reference implementation, so callers can swap repositories
    without observing different behavior.

    Domain trace serialization remains the responsibility of
    DeploymentGovernanceTracePersistenceMapper.
    """

    _SELECT_COLUMNS = """
        trace_id,
        deployment_id,
        service_name,
        environment,
        artifact_digest,
        created_at,
        updated_at,
        governance_state,
        final_status,
        completed,
        payload,
        integrity_algorithm,
        integrity_version,
        integrity_digest
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
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceRecord:
        """
        Persist a new governance trace record.

        Existing trace identifiers and deployment identifiers are rejected.
        """

        parameters = self._record_to_parameters(record)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                    (
                        trace_id,
                        deployment_id,
                        service_name,
                        environment,
                        artifact_digest,
                        created_at,
                        updated_at,
                        governance_state,
                        final_status,
                        completed,
                        payload,
                        integrity_algorithm,
                        integrity_version,
                        integrity_digest
                    )
                    VALUES
                    (
                        :trace_id,
                        :deployment_id,
                        :service_name,
                        :environment,
                        :artifact_digest,
                        :created_at,
                        :updated_at,
                        :governance_state,
                        :final_status,
                        :completed,
                        :payload,
                        :integrity_algorithm,
                        :integrity_version,
                        :integrity_digest
                    )
                    """,
                    parameters,
                )

        except sqlite3.IntegrityError as exc:
            self._raise_integrity_error(record=record, error=exc)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save deployment governance trace "
                f"'{record.trace_id}'"
            ) from exc

        return record

    def save_many(
        self,
        records: Sequence[GovernanceTraceRecord],
    ) -> tuple[GovernanceTraceRecord, ...]:
        """
        Persist multiple governance trace records atomically.

        If any record violates repository constraints, the entire batch is
        rolled back.
        """

        records = tuple(records)

        if not records:
            return ()

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:

                for record in records:
                    parameters = self._record_to_parameters(record)

                    try:
                        connection.execute(
                            f"""
                            INSERT INTO
                            {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                            (
                                trace_id,
                                deployment_id,
                                service_name,
                                environment,
                                artifact_digest,
                                created_at,
                                updated_at,
                                governance_state,
                                final_status,
                                completed,
                                payload,
                                integrity_algorithm,
                                integrity_version,
                                integrity_digest
                            )
                            VALUES
                            (
                                :trace_id,
                                :deployment_id,
                                :service_name,
                                :environment,
                                :artifact_digest,
                                :created_at,
                                :updated_at,
                                :governance_state,
                                :final_status,
                                :completed,
                                :payload,
                                :integrity_algorithm,
                                :integrity_version,
                                :integrity_digest
                            )
                            """,
                            parameters,
                        )

                    except sqlite3.IntegrityError as exc:
                        self._raise_integrity_error(
                            record=record,
                            error=exc,
                        )

        except (
            GovernanceTraceAlreadyExistsError,
            GovernanceTraceNotFoundError,
        ):
            raise

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save deployment governance trace batch"
            ) from exc

        return records

    def update(
        self,
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceRecord:
        """
        Replace the persisted representation of an existing trace.
        """

        parameters = self._record_to_parameters(record)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:

                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                    SET
                        deployment_id = :deployment_id,
                        service_name = :service_name,
                        environment = :environment,
                        artifact_digest = :artifact_digest,
                        created_at = :created_at,
                        updated_at = :updated_at,
                        governance_state = :governance_state,
                        final_status = :final_status,
                        completed = :completed,
                        payload = :payload,
                        integrity_algorithm = :integrity_algorithm,
                        integrity_version = :integrity_version,
                        integrity_digest = :integrity_digest
                    WHERE
                        trace_id = :trace_id
                    """,
                    parameters,
                )

                if cursor.rowcount == 0:
                    raise GovernanceTraceNotFoundError(
                        f"governance trace '{record.trace_id}' does not exist"
                    )

        except GovernanceTraceNotFoundError:
            raise

        except sqlite3.IntegrityError as exc:
            self._raise_integrity_error(record=record, error=exc)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update deployment governance trace "
                f"'{record.trace_id}'"
            ) from exc

        return record

    def upsert(
        self,
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceRecord:
        """
        Insert or replace a governance trace using trace_id as the conflict
        key. Deployment identifier uniqueness remains independently enforced.
        """

        parameters = self._record_to_parameters(record)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:

                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                    (
                        trace_id,
                        deployment_id,
                        service_name,
                        environment,
                        artifact_digest,
                        created_at,
                        updated_at,
                        governance_state,
                        final_status,
                        completed,
                        payload,
                        integrity_algorithm,
                        integrity_version,
                        integrity_digest
                    )
                    VALUES
                    (
                        :trace_id,
                        :deployment_id,
                        :service_name,
                        :environment,
                        :artifact_digest,
                        :created_at,
                        :updated_at,
                        :governance_state,
                        :final_status,
                        :completed,
                        :payload,
                        :integrity_algorithm,
                        :integrity_version,
                        :integrity_digest
                    )
                    ON CONFLICT(trace_id)
                    DO UPDATE SET
                        deployment_id = excluded.deployment_id,
                        service_name = excluded.service_name,
                        environment = excluded.environment,
                        artifact_digest = excluded.artifact_digest,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        governance_state = excluded.governance_state,
                        final_status = excluded.final_status,
                        completed = excluded.completed,
                        payload = excluded.payload,
                        integrity_algorithm = excluded.integrity_algorithm,
                        integrity_version = excluded.integrity_version,
                        integrity_digest = excluded.integrity_digest
                    """,
                    parameters,
                )

        except sqlite3.IntegrityError as exc:
            self._raise_integrity_error(record=record, error=exc)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to upsert deployment governance trace "
                f"'{record.trace_id}'"
            ) from exc

        return record

    def get_by_trace_id(
        self,
        trace_id: str,
    ) -> GovernanceTraceRecord | None:

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                WHERE
                    trace_id = ?
                """,
                (trace_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve deployment governance trace "
                f"'{trace_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def get_by_deployment_id(
        self,
        deployment_id: str,
    ) -> GovernanceTraceRecord | None:

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                WHERE
                    deployment_id = ?
                """,
                (deployment_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance trace for deployment "
                f"'{deployment_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def exists(
        self,
        trace_id: str,
    ) -> bool:

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    1 AS present
                FROM
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                WHERE
                    trace_id = ?
                LIMIT 1
                """,
                (trace_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to check deployment governance trace existence"
            ) from exc

        return row is not None

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[GovernanceTraceRecord, ...]:
        """
        Return governance records in deterministic newest-first order,
        matching the in-memory repository's ordering (created_at DESC,
        trace_id DESC).
        """

        self._validate_pagination(limit=limit, offset=offset)

        sql = f"""
            SELECT
                {self._SELECT_COLUMNS}
            FROM
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
            ORDER BY
                created_at DESC,
                trace_id DESC
        """

        parameters: list[Any] = []

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((limit, offset))
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            parameters.append(offset)

        try:
            rows = self._database.query_all(sql, parameters)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list deployment governance traces"
            ) from exc

        return tuple(self._row_to_record(row) for row in rows)

    def query(
        self,
        query: GovernanceTraceQuery,
    ) -> tuple[GovernanceTraceRecord, ...]:
        """
        Query governance traces using storage-independent repository criteria.
        """

        where_clauses, parameters = self._build_filters(query)

        sql = f"""
            SELECT
                {self._SELECT_COLUMNS}
            FROM
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
        """

        if where_clauses:
            sql += "\nWHERE\n    " + "\n    AND ".join(where_clauses)

        sql += """
            ORDER BY
                created_at DESC,
                trace_id DESC
        """

        if query.limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((query.limit, query.offset))
        elif query.offset:
            sql += " LIMIT -1 OFFSET ?"
            parameters.append(query.offset)

        try:
            rows = self._database.query_all(sql, parameters)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to query deployment governance traces"
            ) from exc

        return tuple(self._row_to_record(row) for row in rows)

    def count(
        self,
        query: GovernanceTraceQuery | None = None,
    ) -> int:
        """
        Count all governance traces or traces matching repository criteria.

        Pagination (limit/offset) is intentionally ignored for aggregate
        counting, matching the in-memory repository's count() semantics.
        """

        if query is None:
            try:
                row = self._database.query_one(
                    f"""
                    SELECT
                        COUNT(*) AS count
                    FROM
                        {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                    """
                )

            except sqlite3.Error as exc:
                raise SQLitePersistenceError(
                    "failed to count deployment governance traces"
                ) from exc

            return 0 if row is None else int(row["count"])

        where_clauses, parameters = self._build_filters(query)

        sql = f"""
            SELECT
                COUNT(*) AS count
            FROM
                {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
        """

        if where_clauses:
            sql += "\nWHERE\n    " + "\n    AND ".join(where_clauses)

        try:
            row = self._database.query_one(sql, parameters)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to count matching deployment governance traces"
            ) from exc

        return 0 if row is None else int(row["count"])

    def statistics(self) -> GovernanceTraceRepositoryStatistics:
        """
        Calculate aggregate governance statistics from durable SQLite state.

        Mirrors the in-memory repository's statistics() exactly: totals plus
        counts derived from `completed` and `final_status`.
        """

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    COUNT(*) AS total_traces,
                    COALESCE(SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END), 0)
                        AS completed_traces,
                    COALESCE(SUM(CASE WHEN completed = 0 THEN 1 ELSE 0 END), 0)
                        AS active_traces,
                    COALESCE(SUM(CASE WHEN final_status = 'succeeded' THEN 1 ELSE 0 END), 0)
                        AS succeeded_traces,
                    COALESCE(SUM(CASE WHEN final_status = 'failed' THEN 1 ELSE 0 END), 0)
                        AS failed_traces,
                    COALESCE(SUM(CASE WHEN final_status = 'blocked' THEN 1 ELSE 0 END), 0)
                        AS blocked_traces,
                    COALESCE(SUM(CASE WHEN final_status = 'rejected' THEN 1 ELSE 0 END), 0)
                        AS rejected_traces
                FROM
                    {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to calculate deployment governance statistics"
            ) from exc

        if row is None:
            return GovernanceTraceRepositoryStatistics(
                total_traces=0,
                completed_traces=0,
                active_traces=0,
                succeeded_traces=0,
                failed_traces=0,
                blocked_traces=0,
                rejected_traces=0,
            )

        return GovernanceTraceRepositoryStatistics(
            total_traces=int(row["total_traces"]),
            completed_traces=int(row["completed_traces"]),
            active_traces=int(row["active_traces"]),
            succeeded_traces=int(row["succeeded_traces"]),
            failed_traces=int(row["failed_traces"]),
            blocked_traces=int(row["blocked_traces"]),
            rejected_traces=int(row["rejected_traces"]),
        )

    def delete(
        self,
        trace_id: str,
    ) -> bool:

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:

                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                    WHERE
                        trace_id = ?
                    """,
                    (trace_id,),
                )

                return cursor.rowcount > 0

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete deployment governance trace "
                f"'{trace_id}'"
            ) from exc

    def clear(self) -> int:
        """
        Delete all persisted governance traces.

        Not part of the abstract repository contract (mirrors the in-memory
        repository's clear(), which is likewise implementation-specific).
        Intended for tests and explicit administrative reset flows, not
        normal application runtime use.
        """

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:

                cursor = connection.execute(
                    f"DELETE FROM {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}"
                )

                return max(cursor.rowcount, 0)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to clear deployment governance traces"
            ) from exc

    def iter_integrity_audit_candidates(
        self,
        *,
        batch_size: int = 500,
    ) -> tuple[
        GovernanceTraceIntegrityAuditCandidate,
        ...
    ]:
        """
        Return raw persisted governance trace candidates for integrity
        auditing.

        Normal read-time integrity verification (via _row_to_record) is
        intentionally bypassed here so an audit can inspect every row and
        report all failures in one pass, rather than raising on the first
        corrupted record.
        """

        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        candidates: list[
            GovernanceTraceIntegrityAuditCandidate
        ] = []

        offset = 0

        while True:
            try:
                rows = self._database.query_all(
                    f"""
                    SELECT
                        {self._SELECT_COLUMNS}
                    FROM
                        {DEPLOYMENT_GOVERNANCE_TRACE_TABLE}
                    ORDER BY
                        trace_id ASC
                    LIMIT ?
                    OFFSET ?
                    """,
                    (
                        batch_size,
                        offset,
                    ),
                )

            except sqlite3.Error as exc:
                raise SQLitePersistenceError(
                    "failed to read deployment governance "
                    "traces for integrity audit"
                ) from exc

            if not rows:
                break

            for row in rows:
                candidates.append(
                    self._row_to_integrity_audit_candidate(
                        row
                    )
                )

            if len(rows) < batch_size:
                break

            offset += len(rows)

        return tuple(candidates)

    def _row_to_integrity_audit_candidate(
        self,
        row: Mapping[str, Any],
    ) -> GovernanceTraceIntegrityAuditCandidate:
        """
        Convert one SQLite row into an integrity audit candidate.

        This method reconstructs the persistence record but deliberately
        does not verify its integrity digest.
        """

        trace_id = str(row["trace_id"])

        integrity_algorithm = (
            None
            if row["integrity_algorithm"] is None
            else str(row["integrity_algorithm"])
        )

        integrity_version = (
            None
            if row["integrity_version"] is None
            else int(row["integrity_version"])
        )

        integrity_digest = (
            None
            if row["integrity_digest"] is None
            else str(row["integrity_digest"])
        )

        try:
            payload = json.loads(str(row["payload"]))

            if not isinstance(payload, dict):
                raise ValueError(
                    "payload must decode to a JSON object"
                )

            record = GovernanceTraceRecord(
                trace_id=trace_id,
                deployment_id=str(row["deployment_id"]),
                service_name=str(row["service_name"]),
                environment=str(row["environment"]),
                artifact_digest=str(row["artifact_digest"]),
                created_at=self._datetime_from_storage(
                    str(row["created_at"])
                ),
                updated_at=self._datetime_from_storage(
                    str(row["updated_at"])
                ),
                governance_state=str(row["governance_state"]),
                final_status=(
                    None
                    if row["final_status"] is None
                    else str(row["final_status"])
                ),
                completed=bool(int(row["completed"])),
                payload=payload,
            )

        except (
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            return GovernanceTraceIntegrityAuditCandidate(
                trace_id=trace_id,
                record=None,
                integrity_algorithm=integrity_algorithm,
                integrity_version=integrity_version,
                integrity_digest=integrity_digest,
                reconstruction_error=str(exc),
            )

        return GovernanceTraceIntegrityAuditCandidate(
            trace_id=trace_id,
            record=record,
            integrity_algorithm=integrity_algorithm,
            integrity_version=integrity_version,
            integrity_digest=integrity_digest,
            reconstruction_error=None,
        )

    def _record_to_parameters(
        self,
        record: GovernanceTraceRecord,
    ) -> dict[str, Any]:

        try:
            payload = json.dumps(
                record.payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )

        except (TypeError, ValueError) as exc:
            raise SQLiteGovernanceTraceSerializationError(
                "deployment governance trace payload is not "
                "JSON serializable"
            ) from exc

        integrity = DeploymentGovernanceTraceIntegrity.calculate(record)

        return {
            "trace_id": record.trace_id,
            "deployment_id": record.deployment_id,
            "service_name": record.service_name,
            "environment": record.environment,
            "artifact_digest": record.artifact_digest,
            "created_at": self._datetime_to_storage(record.created_at),
            "updated_at": self._datetime_to_storage(record.updated_at),
            "governance_state": record.governance_state,
            "final_status": record.final_status,
            "completed": 1 if record.completed else 0,
            "payload": payload,
            "integrity_algorithm": integrity.algorithm,
            "integrity_version": integrity.version,
            "integrity_digest": integrity.digest,
        }

    def _row_to_record(
        self,
        row: Mapping[str, Any],
    ) -> GovernanceTraceRecord:

        try:
            payload = json.loads(str(row["payload"]))

        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SQLiteGovernanceTraceSerializationError(
                "persisted deployment governance trace contains "
                "invalid JSON payload"
            ) from exc

        if not isinstance(payload, dict):
            raise SQLiteGovernanceTraceSerializationError(
                "persisted deployment governance trace payload "
                "must decode to an object"
            )

        try:
            record = GovernanceTraceRecord(
                trace_id=str(row["trace_id"]),
                deployment_id=str(row["deployment_id"]),
                service_name=str(row["service_name"]),
                environment=str(row["environment"]),
                artifact_digest=str(row["artifact_digest"]),
                created_at=self._datetime_from_storage(
                    str(row["created_at"])
                ),
                updated_at=self._datetime_from_storage(
                    str(row["updated_at"])
                ),
                governance_state=str(row["governance_state"]),
                final_status=(
                    None
                    if row["final_status"] is None
                    else str(row["final_status"])
                ),
                completed=bool(int(row["completed"])),
                payload=payload,
            )

        except (TypeError, ValueError) as exc:
            raise SQLiteGovernanceTraceSerializationError(
                "persisted deployment governance trace row "
                "cannot be reconstructed"
            ) from exc

        integrity_algorithm = row["integrity_algorithm"]
        integrity_version = row["integrity_version"]
        integrity_digest = row["integrity_digest"]

        if (
            integrity_algorithm is None
            or integrity_version is None
            or integrity_digest is None
        ):
            raise GovernanceTraceIntegrityMetadataMissingError(
                "persisted deployment governance trace "
                f"'{record.trace_id}' has no integrity metadata"
            )

        metadata = GovernanceTraceIntegrityMetadata(
            algorithm=str(integrity_algorithm),
            version=int(integrity_version),
            digest=str(integrity_digest),
        )

        DeploymentGovernanceTraceIntegrity.verify(record, metadata)

        return record

    def _raise_integrity_error(
        self,
        *,
        record: GovernanceTraceRecord,
        error: sqlite3.IntegrityError,
    ) -> None:
        """
        Translate SQLite integrity failures into the same
        GovernanceTraceAlreadyExistsError raised by the in-memory repository,
        so callers see identical repository-level errors regardless of the
        underlying storage backend.
        """

        message = str(error).lower()

        if "trace_id" in message or "primary key" in message:
            raise GovernanceTraceAlreadyExistsError(
                f"governance trace '{record.trace_id}' already exists"
            ) from error

        if "deployment_id" in message:
            existing = self.get_by_deployment_id(record.deployment_id)

            existing_trace_id = (
                existing.trace_id
                if existing is not None
                else "unknown"
            )

            raise GovernanceTraceAlreadyExistsError(
                "deployment "
                f"'{record.deployment_id}' is already associated with "
                f"governance trace '{existing_trace_id}'"
            ) from error

        raise SQLitePersistenceError(
            "deployment governance trace violated "
            "a SQLite integrity constraint"
        ) from error

    def _build_filters(
        self,
        query: GovernanceTraceQuery,
    ) -> tuple[list[str], list[Any]]:
        where_clauses: list[str] = []
        parameters: list[Any] = []

        self._append_equality_filter(
            where_clauses=where_clauses,
            parameters=parameters,
            column="deployment_id",
            value=query.deployment_id,
        )

        self._append_equality_filter(
            where_clauses=where_clauses,
            parameters=parameters,
            column="service_name",
            value=query.service_name,
        )

        self._append_equality_filter(
            where_clauses=where_clauses,
            parameters=parameters,
            column="environment",
            value=query.environment,
        )

        self._append_equality_filter(
            where_clauses=where_clauses,
            parameters=parameters,
            column="governance_state",
            value=query.governance_state,
        )

        self._append_equality_filter(
            where_clauses=where_clauses,
            parameters=parameters,
            column="final_status",
            value=query.final_status,
        )

        if query.completed is not None:
            where_clauses.append("completed = ?")
            parameters.append(1 if query.completed else 0)

        if query.created_after is not None:
            where_clauses.append("created_at >= ?")
            parameters.append(
                self._datetime_to_storage(query.created_after)
            )

        if query.created_before is not None:
            where_clauses.append("created_at <= ?")
            parameters.append(
                self._datetime_to_storage(query.created_before)
            )

        return where_clauses, parameters

    @staticmethod
    def _append_equality_filter(
        *,
        where_clauses: list[str],
        parameters: list[Any],
        column: str,
        value: Any,
    ) -> None:
        if value is None:
            return

        where_clauses.append(f"{column} = ?")
        parameters.append(value)

    @staticmethod
    def _validate_pagination(
        *,
        limit: int | None,
        offset: int,
    ) -> None:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be greater than zero")

        if offset < 0:
            raise ValueError("offset cannot be negative")

    @staticmethod
    def _datetime_to_storage(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        return value.isoformat()

    @staticmethod
    def _datetime_from_storage(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

        except ValueError as exc:
            raise SQLiteGovernanceTraceSerializationError(
                "persisted deployment governance trace contains "
                f"invalid datetime '{value}'"
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)
