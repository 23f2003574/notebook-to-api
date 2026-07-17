from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_delivery_engine import (
    GovernanceIntegrityDeliveryStatus,
)
from .deployment_governance_delivery_history import (
    GovernanceIntegrityDeliveryHistoryRecord,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityDeliveryHistoryRepository:
    """
    Durable SQLite implementation of governance audit delivery
    history storage.

    Conforms structurally to
    GovernanceIntegrityDeliveryHistoryRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityDeliveryHistoryRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "delivery_id, dispatch_id, channel_name, status, "
        "delivered_at, error"
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
        record: GovernanceIntegrityDeliveryHistoryRecord,
    ) -> GovernanceIntegrityDeliveryHistoryRecord:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE}
                    (
                        delivery_id,
                        dispatch_id,
                        channel_name,
                        status,
                        delivered_at,
                        error
                    )
                    VALUES
                    (
                        :delivery_id,
                        :dispatch_id,
                        :channel_name,
                        :status,
                        :delivered_at,
                        :error
                    )
                    ON CONFLICT (delivery_id) DO UPDATE SET
                        dispatch_id = excluded.dispatch_id,
                        channel_name = excluded.channel_name,
                        status = excluded.status,
                        delivered_at = excluded.delivered_at,
                        error = excluded.error
                    """,
                    {
                        "delivery_id": record.delivery_id,
                        "dispatch_id": record.dispatch_id,
                        "channel_name": record.channel_name,
                        "status": record.status.value,
                        "delivered_at": self._datetime_to_storage(
                            record.delivered_at
                        ),
                        "error": record.error,
                    },
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit delivery history "
                f"record '{record.delivery_id}'"
            ) from exc

        return record

    def get(
        self,
        delivery_id: str,
    ) -> GovernanceIntegrityDeliveryHistoryRecord | None:
        normalized_id = self._normalize(delivery_id)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE}
                WHERE
                    delivery_id = ?
                """,
                (normalized_id,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit delivery "
                f"history record '{normalized_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryHistoryRecord,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE}
                ORDER BY
                    delivered_at DESC,
                    delivery_id DESC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit delivery history"
            ) from exc

        return tuple(self._row_to_record(row) for row in rows)

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
                        {DEPLOYMENT_GOVERNANCE_DELIVERY_HISTORY_TABLE}
                    """
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to clear governance audit delivery history"
            ) from exc

    @staticmethod
    def _normalize(delivery_id: str) -> str:
        normalized_id = delivery_id.strip()

        if not normalized_id:
            raise ValueError(
                "delivery_id must not be empty"
            )

        return normalized_id

    @classmethod
    def _row_to_record(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityDeliveryHistoryRecord:
        error = row["error"]

        return GovernanceIntegrityDeliveryHistoryRecord(
            delivery_id=str(row["delivery_id"]),
            dispatch_id=str(row["dispatch_id"]),
            channel_name=str(row["channel_name"]),
            status=GovernanceIntegrityDeliveryStatus(
                row["status"]
            ),
            delivered_at=cls._datetime_from_storage(
                str(row["delivered_at"])
            ),
            error=None if error is None else str(error),
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
