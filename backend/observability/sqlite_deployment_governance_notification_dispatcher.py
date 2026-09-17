from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_notification_dispatcher import (
    GovernanceIntegrityDispatchStatus,
    GovernanceIntegrityNotificationDispatch,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityNotificationDispatchRepository:
    """
    Durable SQLite implementation of governance audit notification
    dispatch storage.

    Conforms structurally to
    GovernanceIntegrityNotificationDispatchRepository so callers can
    swap this repository for
    InMemoryGovernanceIntegrityNotificationDispatchRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "dispatch_id, notification_id, channel_name, status, "
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
        dispatch: GovernanceIntegrityNotificationDispatch,
    ) -> GovernanceIntegrityNotificationDispatch:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                    (
                        dispatch_id,
                        notification_id,
                        channel_name,
                        status,
                        created_at
                    )
                    VALUES
                    (
                        :dispatch_id,
                        :notification_id,
                        :channel_name,
                        :status,
                        :created_at
                    )
                    ON CONFLICT (dispatch_id) DO UPDATE SET
                        notification_id = excluded.notification_id,
                        channel_name = excluded.channel_name,
                        status = excluded.status,
                        created_at = excluded.created_at
                    """,
                    {
                        "dispatch_id": dispatch.dispatch_id,
                        "notification_id": dispatch.notification_id,
                        "channel_name": dispatch.channel_name,
                        "status": dispatch.status.value,
                        "created_at": self._datetime_to_storage(
                            dispatch.created_at
                        ),
                    },
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit notification "
                f"dispatch '{dispatch.dispatch_id}'"
            ) from exc

        return dispatch

    def get(
        self,
        dispatch_id: str,
    ) -> GovernanceIntegrityNotificationDispatch | None:
        normalized_id = self._normalize(dispatch_id)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                WHERE
                    dispatch_id = ?
                """,
                (normalized_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit notification "
                f"dispatch '{normalized_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_dispatch(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationDispatch,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                ORDER BY
                    created_at DESC,
                    dispatch_id DESC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit notification "
                "dispatches"
            ) from exc

        return tuple(self._row_to_dispatch(row) for row in rows)

    def delete(
        self,
        dispatch_id: str,
    ) -> None:
        normalized_id = self._normalize(dispatch_id)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                    WHERE
                        dispatch_id = ?
                    """,
                    (normalized_id,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit notification "
                f"dispatch '{normalized_id}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"notification dispatch '{normalized_id}' was not "
                "found"
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
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_DISPATCH_TABLE}
                    """
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to clear governance audit notification "
                "dispatches"
            ) from exc

    @staticmethod
    def _normalize(dispatch_id: str) -> str:
        normalized_id = dispatch_id.strip()

        if not normalized_id:
            raise ValueError(
                "dispatch_id must not be empty"
            )

        return normalized_id

    @classmethod
    def _row_to_dispatch(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityNotificationDispatch:
        return GovernanceIntegrityNotificationDispatch(
            dispatch_id=str(row["dispatch_id"]),
            notification_id=str(row["notification_id"]),
            channel_name=str(row["channel_name"]),
            status=GovernanceIntegrityDispatchStatus(
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
