from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertSeverity,
)
from .deployment_governance_notifications import (
    GovernanceIntegrityNotification,
    GovernanceIntegrityNotificationStatus,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityNotificationRepository:
    """
    Durable SQLite implementation of governance audit notification
    storage.

    Conforms structurally to GovernanceIntegrityNotificationRepository
    so callers can swap this repository for
    InMemoryGovernanceIntegrityNotificationRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "notification_id, alert_id, severity, message, status, "
        "created_at"
    )

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
        notification: GovernanceIntegrityNotification,
    ) -> GovernanceIntegrityNotification:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                    (
                        notification_id,
                        alert_id,
                        severity,
                        message,
                        status,
                        created_at
                    )
                    VALUES
                    (
                        :notification_id,
                        :alert_id,
                        :severity,
                        :message,
                        :status,
                        :created_at
                    )
                    ON CONFLICT (notification_id) DO UPDATE SET
                        alert_id = excluded.alert_id,
                        severity = excluded.severity,
                        message = excluded.message,
                        status = excluded.status,
                        created_at = excluded.created_at
                    """,
                    {
                        "notification_id": (
                            notification.notification_id
                        ),
                        "alert_id": notification.alert_id,
                        "severity": notification.severity.value,
                        "message": notification.message,
                        "status": notification.status.value,
                        "created_at": self._datetime_to_storage(
                            notification.created_at
                        ),
                    },
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit notification "
                f"'{notification.notification_id}'"
            ) from exc

        return notification

    def get(
        self,
        notification_id: str,
    ) -> GovernanceIntegrityNotification | None:
        normalized_id = self._normalize(notification_id)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                WHERE
                    notification_id = ?
                """,
                (normalized_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit notification "
                f"'{normalized_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_notification(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotification,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                ORDER BY
                    created_at DESC,
                    notification_id DESC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit notifications"
            ) from exc

        return tuple(self._row_to_notification(row) for row in rows)

    def delete(
        self,
        notification_id: str,
    ) -> None:
        normalized_id = self._normalize(notification_id)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                    WHERE
                        notification_id = ?
                    """,
                    (normalized_id,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit notification "
                f"'{normalized_id}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"notification '{normalized_id}' was not found"
            )

    def clear(
        self,
    ) -> None:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_TABLE}
                    """
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to clear governance audit notifications"
            ) from exc

    @staticmethod
    def _normalize(notification_id: str) -> str:
        normalized_id = notification_id.strip()

        if not normalized_id:
            raise ValueError(
                "notification_id must not be empty"
            )

        return normalized_id

    @classmethod
    def _row_to_notification(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityNotification:
        return GovernanceIntegrityNotification(
            notification_id=str(row["notification_id"]),
            alert_id=str(row["alert_id"]),
            severity=GovernanceIntegrityAlertSeverity(
                row["severity"]
            ),
            message=str(row["message"]),
            status=GovernanceIntegrityNotificationStatus(
                row["status"]
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
