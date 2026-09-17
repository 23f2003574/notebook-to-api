from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannel,
    GovernanceIntegrityNotificationChannelAlreadyExistsError,
    GovernanceIntegrityNotificationChannelType,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityNotificationChannelRepository:
    """
    Durable SQLite implementation of governance audit notification
    channel storage.

    Conforms structurally to
    GovernanceIntegrityNotificationChannelRepository so callers can
    swap this repository for
    InMemoryGovernanceIntegrityNotificationChannelRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "name, channel_type, destination, enabled, created_at"
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
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityNotificationChannel:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE}
                    (
                        name,
                        channel_type,
                        destination,
                        enabled,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :channel_type,
                        :destination,
                        :enabled,
                        :created_at
                    )
                    """,
                    self._channel_to_parameters(channel),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityNotificationChannelAlreadyExistsError(
                    f"notification channel '{channel.name}' "
                    "already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit notification "
                f"channel '{channel.name}'"
            ) from exc

        return channel

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationChannel | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit notification "
                f"channel '{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_channel(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationChannel,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit notification "
                "channels"
            ) from exc

        return tuple(self._row_to_channel(row) for row in rows)

    def update(
        self,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> GovernanceIntegrityNotificationChannel:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE}
                    SET
                        channel_type = :channel_type,
                        destination = :destination,
                        enabled = :enabled,
                        created_at = :created_at
                    WHERE
                        name = :name
                    """,
                    self._channel_to_parameters(channel),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit notification "
                f"channel '{channel.name}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                f"notification channel '{channel.name}' was not found"
            )

        return channel

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
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_CHANNEL_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit notification "
                f"channel '{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"notification channel '{normalized_name}' was not "
                "found"
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
    def _channel_to_parameters(
        cls,
        channel: GovernanceIntegrityNotificationChannel,
    ) -> dict[str, Any]:
        return {
            "name": channel.name,
            "channel_type": channel.channel_type.value,
            "destination": channel.destination,
            "enabled": 1 if channel.enabled else 0,
            "created_at": cls._datetime_to_storage(
                channel.created_at
            ),
        }

    @classmethod
    def _row_to_channel(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityNotificationChannel:
        return GovernanceIntegrityNotificationChannel(
            name=str(row["name"]),
            channel_type=GovernanceIntegrityNotificationChannelType(
                row["channel_type"]
            ),
            destination=str(row["destination"]),
            enabled=bool(row["enabled"]),
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
