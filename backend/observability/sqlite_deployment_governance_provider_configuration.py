from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from .deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfiguration,
    GovernanceIntegrityProviderConfigurationAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityProviderConfigurationRepository:
    """
    Durable SQLite implementation of governance audit provider
    configuration storage.

    Conforms structurally to
    GovernanceIntegrityProviderConfigurationRepository so callers can
    swap this repository for
    InMemoryGovernanceIntegrityProviderConfigurationRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = "channel_type, configuration_json, updated_at"

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
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> GovernanceIntegrityProviderConfiguration:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE}
                    (
                        channel_type,
                        configuration_json,
                        updated_at
                    )
                    VALUES
                    (
                        :channel_type,
                        :configuration_json,
                        :updated_at
                    )
                    """,
                    self._configuration_to_parameters(configuration),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityProviderConfigurationAlreadyExistsError(
                    "provider configuration for channel type "
                    f"'{configuration.channel_type.value}' already "
                    "exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit provider "
                f"configuration for channel type "
                f"'{configuration.channel_type.value}'"
            ) from exc

        return configuration

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderConfiguration | None:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE}
                WHERE
                    channel_type = ?
                """,
                (channel_type.value,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit provider "
                f"configuration for channel type "
                f"'{channel_type.value}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_configuration(row)

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderConfiguration, ...]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE}
                ORDER BY
                    channel_type ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit provider "
                "configurations"
            ) from exc

        return tuple(
            self._row_to_configuration(row) for row in rows
        )

    def update(
        self,
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> GovernanceIntegrityProviderConfiguration:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE}
                    SET
                        configuration_json = :configuration_json,
                        updated_at = :updated_at
                    WHERE
                        channel_type = :channel_type
                    """,
                    self._configuration_to_parameters(configuration),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit provider "
                f"configuration for channel type "
                f"'{configuration.channel_type.value}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                "provider configuration for channel type "
                f"'{configuration.channel_type.value}' was not found"
            )

        return configuration

    def delete(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> None:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_PROVIDER_CONFIGURATION_TABLE}
                    WHERE
                        channel_type = ?
                    """,
                    (channel_type.value,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit provider "
                f"configuration for channel type "
                f"'{channel_type.value}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                "provider configuration for channel type "
                f"'{channel_type.value}' was not found"
            )

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        return self.get(channel_type) is not None

    @classmethod
    def _configuration_to_parameters(
        cls,
        configuration: GovernanceIntegrityProviderConfiguration,
    ) -> dict[str, Any]:
        return {
            "channel_type": configuration.channel_type.value,
            "configuration_json": json.dumps(
                dict(configuration.values)
            ),
            "updated_at": cls._datetime_to_storage(
                configuration.updated_at
            ),
        }

    @classmethod
    def _row_to_configuration(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityProviderConfiguration:
        return GovernanceIntegrityProviderConfiguration(
            channel_type=GovernanceIntegrityNotificationChannelType(
                str(row["channel_type"])
            ),
            values=json.loads(str(row["configuration_json"])),
            updated_at=cls._datetime_from_storage(
                str(row["updated_at"])
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
