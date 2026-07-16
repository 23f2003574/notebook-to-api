from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_bookmarks import (
    GovernanceIntegrityAuditBookmark,
    GovernanceIntegrityAuditBookmarkAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityAuditBookmarkRepository:
    """
    Durable SQLite implementation of governance audit bookmark storage.

    Conforms structurally to GovernanceIntegrityAuditBookmarkRepository so
    callers can swap this repository for
    InMemoryGovernanceIntegrityAuditBookmarkRepository without observing
    different behavior.
    """

    _SELECT_COLUMNS = "name, audit_id, created_at"

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
        bookmark: GovernanceIntegrityAuditBookmark,
    ) -> GovernanceIntegrityAuditBookmark:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE}
                    (
                        name,
                        audit_id,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :audit_id,
                        :created_at
                    )
                    """,
                    {
                        "name": bookmark.name,
                        "audit_id": bookmark.audit_id,
                        "created_at": self._datetime_to_storage(
                            bookmark.created_at
                        ),
                    },
                )

        except sqlite3.IntegrityError as exc:
            raise GovernanceIntegrityAuditBookmarkAlreadyExistsError(
                f"governance audit bookmark '{bookmark.name}' "
                "already exists"
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit bookmark "
                f"'{bookmark.name}'"
            ) from exc

        return bookmark

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditBookmark | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit bookmark "
                f"'{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_bookmark(row)

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
                        {DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit bookmark "
                f"'{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"governance audit bookmark '{normalized_name}' "
                "was not found"
            )

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditBookmark,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_BOOKMARK_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit bookmarks"
            ) from exc

        return tuple(self._row_to_bookmark(row) for row in rows)

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
    def _row_to_bookmark(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityAuditBookmark:
        return GovernanceIntegrityAuditBookmark(
            name=str(row["name"]),
            audit_id=str(row["audit_id"]),
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
