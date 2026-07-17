from __future__ import annotations

import json
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
from .deployment_governance_notification_preferences import (
    GovernanceIntegrityNotificationPreference,
    GovernanceIntegrityNotificationPreferenceAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityNotificationPreferenceRepository:
    """
    Durable SQLite implementation of governance audit notification
    preference storage.

    Conforms structurally to
    GovernanceIntegrityNotificationPreferenceRepository so callers can
    swap this repository for
    InMemoryGovernanceIntegrityNotificationPreferenceRepository
    without observing different behavior.
    """

    _SELECT_COLUMNS = (
        "name, minimum_severity, channels, enabled, created_at"
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
        preference: GovernanceIntegrityNotificationPreference,
    ) -> GovernanceIntegrityNotificationPreference:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE}
                    (
                        name,
                        minimum_severity,
                        channels,
                        enabled,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :minimum_severity,
                        :channels,
                        :enabled,
                        :created_at
                    )
                    """,
                    self._preference_to_parameters(preference),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityNotificationPreferenceAlreadyExistsError(
                    f"notification preference '{preference.name}' "
                    "already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit notification "
                f"preference '{preference.name}'"
            ) from exc

        return preference

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityNotificationPreference | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit notification "
                f"preference '{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_preference(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityNotificationPreference,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit notification "
                "preferences"
            ) from exc

        return tuple(self._row_to_preference(row) for row in rows)

    def update(
        self,
        preference: GovernanceIntegrityNotificationPreference,
    ) -> GovernanceIntegrityNotificationPreference:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE}
                    SET
                        minimum_severity = :minimum_severity,
                        channels = :channels,
                        enabled = :enabled,
                        created_at = :created_at
                    WHERE
                        name = :name
                    """,
                    self._preference_to_parameters(preference),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit notification "
                f"preference '{preference.name}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                f"notification preference '{preference.name}' was "
                "not found"
            )

        return preference

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
                        {DEPLOYMENT_GOVERNANCE_NOTIFICATION_PREFERENCE_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit notification "
                f"preference '{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"notification preference '{normalized_name}' was "
                "not found"
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
    def _preference_to_parameters(
        cls,
        preference: GovernanceIntegrityNotificationPreference,
    ) -> dict[str, Any]:
        return {
            "name": preference.name,
            "minimum_severity": preference.minimum_severity.value,
            "channels": json.dumps(list(preference.channels)),
            "enabled": 1 if preference.enabled else 0,
            "created_at": cls._datetime_to_storage(
                preference.created_at
            ),
        }

    @classmethod
    def _row_to_preference(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityNotificationPreference:
        return GovernanceIntegrityNotificationPreference(
            name=str(row["name"]),
            minimum_severity=GovernanceIntegrityAlertSeverity(
                row["minimum_severity"]
            ),
            channels=tuple(json.loads(str(row["channels"]))),
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
