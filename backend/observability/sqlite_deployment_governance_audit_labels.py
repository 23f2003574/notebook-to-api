from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_labels import (
    GovernanceIntegrityAuditLabel,
    GovernanceIntegrityAuditLabelAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityAuditLabelRepository:
    """
    Durable SQLite implementation of governance audit label storage.

    Conforms structurally to GovernanceIntegrityAuditLabelRepository so
    callers can swap this repository for
    InMemoryGovernanceIntegrityAuditLabelRepository without observing
    different behavior.
    """

    _SELECT_COLUMNS = "audit_id, label, created_at"

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

    def add(
        self,
        label: GovernanceIntegrityAuditLabel,
    ) -> GovernanceIntegrityAuditLabel:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                    (
                        audit_id,
                        label,
                        created_at
                    )
                    VALUES
                    (
                        :audit_id,
                        :label,
                        :created_at
                    )
                    """,
                    {
                        "audit_id": label.audit_id,
                        "label": label.label,
                        "created_at": self._datetime_to_storage(
                            label.created_at
                        ),
                    },
                )

        except sqlite3.IntegrityError as exc:
            raise GovernanceIntegrityAuditLabelAlreadyExistsError(
                f"label '{label.label}' is already applied to "
                f"audit '{label.audit_id}'"
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit label "
                f"'{label.label}' for audit '{label.audit_id}'"
            ) from exc

        return label

    def remove(
        self,
        audit_id: str,
        label: str,
    ) -> None:
        normalized_audit_id = self._normalize(audit_id, "audit_id")
        normalized_label = self._normalize(label, "label")

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                    WHERE
                        audit_id = ?
                        AND label = ?
                    """,
                    (normalized_audit_id, normalized_label),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to remove governance audit label "
                f"'{normalized_label}' from audit "
                f"'{normalized_audit_id}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"label '{normalized_label}' was not found on "
                f"audit '{normalized_audit_id}'"
            )

    def labels(
        self,
        audit_id: str,
    ) -> tuple[str, ...]:
        normalized_audit_id = self._normalize(audit_id, "audit_id")

        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                WHERE
                    audit_id = ?
                ORDER BY
                    created_at DESC,
                    label DESC
                """,
                (normalized_audit_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list labels for governance integrity "
                f"audit '{normalized_audit_id}'"
            ) from exc

        return tuple(str(row["label"]) for row in rows)

    def audits(
        self,
        label: str,
    ) -> tuple[str, ...]:
        normalized_label = self._normalize(label, "label")

        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                WHERE
                    label = ?
                ORDER BY
                    created_at DESC,
                    audit_id DESC
                """,
                (normalized_label,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list audits for governance audit label "
                f"'{normalized_label}'"
            ) from exc

        return tuple(str(row["audit_id"]) for row in rows)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditLabel,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_LABEL_TABLE}
                ORDER BY
                    created_at DESC,
                    audit_id DESC,
                    label DESC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit labels"
            ) from exc

        return tuple(self._row_to_label(row) for row in rows)

    @staticmethod
    def _normalize(value: str, field_name: str) -> str:
        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                f"{field_name} must not be empty"
            )

        return normalized_value

    @classmethod
    def _row_to_label(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityAuditLabel:
        return GovernanceIntegrityAuditLabel(
            audit_id=str(row["audit_id"]),
            label=str(row["label"]),
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
