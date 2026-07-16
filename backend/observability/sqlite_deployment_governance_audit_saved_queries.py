from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_saved_queries import (
    GovernanceIntegritySavedAuditQuery,
    GovernanceIntegritySavedAuditQueryAlreadyExistsError,
)
from .deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchQuery,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegritySavedAuditQueryRepository:
    """
    Durable SQLite implementation of saved governance audit query
    storage.

    Conforms structurally to GovernanceIntegritySavedAuditQueryRepository
    so callers can swap this repository for
    InMemoryGovernanceIntegritySavedAuditQueryRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = "name, query_json, created_at"

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

    def save(
        self,
        saved_query: GovernanceIntegritySavedAuditQuery,
    ) -> GovernanceIntegritySavedAuditQuery:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE}
                    (
                        name,
                        query_json,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :query_json,
                        :created_at
                    )
                    """,
                    {
                        "name": saved_query.name,
                        "query_json": json.dumps(
                            saved_query.query.to_dict()
                        ),
                        "created_at": self._datetime_to_storage(
                            saved_query.created_at
                        ),
                    },
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegritySavedAuditQueryAlreadyExistsError(
                    f"saved query '{saved_query.name}' already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit query "
                f"'{saved_query.name}'"
            ) from exc

        return saved_query

    def get(
        self,
        name: str,
    ) -> GovernanceIntegritySavedAuditQuery | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit query "
                f"'{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_saved_query(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegritySavedAuditQuery,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit queries"
            ) from exc

        return tuple(self._row_to_saved_query(row) for row in rows)

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_SAVED_AUDIT_QUERY_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit query "
                f"'{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"saved query '{normalized_name}' was not found"
            )

    def exists(
        self,
        name: str,
    ) -> bool:
        return self.get(name) is not None

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name

    @classmethod
    def _row_to_saved_query(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegritySavedAuditQuery:
        return GovernanceIntegritySavedAuditQuery(
            name=str(row["name"]),
            query=GovernanceIntegrityAuditSearchQuery.from_dict(
                json.loads(str(row["query_json"]))
            ),
            created_at=cls._datetime_from_storage(
                str(row["created_at"])
            ),
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
