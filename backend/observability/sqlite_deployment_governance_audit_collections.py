from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollection,
    GovernanceIntegrityAuditCollectionAlreadyExistsError,
    GovernanceIntegrityAuditCollectionEntry,
    GovernanceIntegrityAuditCollectionEntryAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE,
    DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityAuditCollectionRepository:
    """
    Durable SQLite implementation of governance audit collection storage.

    Conforms structurally to GovernanceIntegrityAuditCollectionRepository
    so callers can swap this repository for
    InMemoryGovernanceIntegrityAuditCollectionRepository without
    observing different behavior.
    """

    _COLLECTION_SELECT_COLUMNS = "name, description, created_at"

    _ENTRY_SELECT_COLUMNS = "collection, audit_id, added_at"

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

    def create(
        self,
        collection: GovernanceIntegrityAuditCollection,
    ) -> GovernanceIntegrityAuditCollection:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE}
                    (
                        name,
                        description,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :description,
                        :created_at
                    )
                    """,
                    {
                        "name": collection.name,
                        "description": collection.description,
                        "created_at": self._datetime_to_storage(
                            collection.created_at
                        ),
                    },
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityAuditCollectionAlreadyExistsError(
                    f"collection '{collection.name}' already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to create governance audit collection "
                f"'{collection.name}'"
            ) from exc

        return collection

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize(name, "name")

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

                if deleted > 0:
                    connection.execute(
                        f"""
                        DELETE FROM
                            {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                        WHERE
                            collection = ?
                        """,
                        (normalized_name,),
                    )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit collection "
                f"'{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"collection '{normalized_name}' was not found"
            )

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditCollection | None:
        normalized_name = self._normalize(name, "name")

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._COLLECTION_SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit collection "
                f"'{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_collection(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditCollection,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._COLLECTION_SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit collections"
            ) from exc

        return tuple(self._row_to_collection(row) for row in rows)

    def add_audit(
        self,
        collection: str,
        audit_id: str,
        *,
        added_at: datetime | None = None,
    ) -> GovernanceIntegrityAuditCollectionEntry:
        normalized_collection = self._normalize(
            collection, "collection"
        )

        normalized_audit_id = self._normalize(audit_id, "audit_id")

        entry = GovernanceIntegrityAuditCollectionEntry(
            collection=normalized_collection,
            audit_id=normalized_audit_id,
            added_at=added_at or datetime.now(timezone.utc),
        )

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                    (
                        collection,
                        audit_id,
                        added_at
                    )
                    VALUES
                    (
                        :collection,
                        :audit_id,
                        :added_at
                    )
                    """,
                    {
                        "collection": entry.collection,
                        "audit_id": entry.audit_id,
                        "added_at": self._datetime_to_storage(
                            entry.added_at
                        ),
                    },
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityAuditCollectionEntryAlreadyExistsError(
                    f"audit '{normalized_audit_id}' is already in "
                    f"collection '{normalized_collection}'"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to add audit '{normalized_audit_id}' to "
                f"collection '{normalized_collection}'"
            ) from exc

        return entry

    def remove_audit(
        self,
        collection: str,
        audit_id: str,
    ) -> None:
        normalized_collection = self._normalize(
            collection, "collection"
        )

        normalized_audit_id = self._normalize(audit_id, "audit_id")

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                    WHERE
                        collection = ?
                        AND audit_id = ?
                    """,
                    (normalized_collection, normalized_audit_id),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to remove audit '{normalized_audit_id}' from "
                f"collection '{normalized_collection}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"audit '{normalized_audit_id}' is not in "
                f"collection '{normalized_collection}'"
            )

    def audits(
        self,
        collection: str,
    ) -> tuple[str, ...]:
        normalized_collection = self._normalize(
            collection, "collection"
        )

        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._ENTRY_SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                WHERE
                    collection = ?
                ORDER BY
                    added_at DESC,
                    audit_id DESC
                """,
                (normalized_collection,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list audits for governance audit collection "
                f"'{normalized_collection}'"
            ) from exc

        return tuple(str(row["audit_id"]) for row in rows)

    def collections(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        normalized_audit_id = self._normalize(audit_id, "audit_id")

        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._ENTRY_SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_COLLECTION_ENTRY_TABLE}
                WHERE
                    audit_id = ?
                ORDER BY
                    added_at DESC,
                    collection DESC
                """,
                (normalized_audit_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list collections for governance integrity "
                f"audit '{normalized_audit_id}'"
            ) from exc

        return tuple(str(row["collection"]) for row in rows)

    @staticmethod
    def _normalize(value: str, field_name: str) -> str:
        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                f"{field_name} must not be empty"
            )

        return normalized_value

    @classmethod
    def _row_to_collection(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityAuditCollection:
        description = row["description"]

        return GovernanceIntegrityAuditCollection(
            name=str(row["name"]),
            description=(
                None if description is None else str(description)
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
