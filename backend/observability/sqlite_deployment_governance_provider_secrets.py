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
from .deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecrets,
    GovernanceIntegrityProviderSecretsAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityProviderSecretsRepository:
    """
    Durable SQLite implementation of governance audit provider
    secrets storage.

    Conforms structurally to
    GovernanceIntegrityProviderSecretsRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityProviderSecretsRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = "channel_type, secrets_json, updated_at"

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
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> GovernanceIntegrityProviderSecrets:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE}
                    (
                        channel_type,
                        secrets_json,
                        updated_at
                    )
                    VALUES
                    (
                        :channel_type,
                        :secrets_json,
                        :updated_at
                    )
                    """,
                    self._secrets_to_parameters(secrets),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityProviderSecretsAlreadyExistsError(
                    "provider secrets for channel type "
                    f"'{secrets.channel_type.value}' already exist"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit provider secrets "
                f"for channel type '{secrets.channel_type.value}'"
            ) from exc

        return secrets

    def get(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> GovernanceIntegrityProviderSecrets | None:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE}
                WHERE
                    channel_type = ?
                """,
                (channel_type.value,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit provider "
                f"secrets for channel type '{channel_type.value}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_secrets(row)

    def list(
        self,
    ) -> tuple[GovernanceIntegrityProviderSecrets, ...]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE}
                ORDER BY
                    channel_type ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit provider secrets"
            ) from exc

        return tuple(self._row_to_secrets(row) for row in rows)

    def update(
        self,
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> GovernanceIntegrityProviderSecrets:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE}
                    SET
                        secrets_json = :secrets_json,
                        updated_at = :updated_at
                    WHERE
                        channel_type = :channel_type
                    """,
                    self._secrets_to_parameters(secrets),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit provider secrets "
                f"for channel type '{secrets.channel_type.value}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                "provider secrets for channel type "
                f"'{secrets.channel_type.value}' were not found"
            )

        return secrets

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
                        {DEPLOYMENT_GOVERNANCE_PROVIDER_SECRETS_TABLE}
                    WHERE
                        channel_type = ?
                    """,
                    (channel_type.value,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit provider secrets "
                f"for channel type '{channel_type.value}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                "provider secrets for channel type "
                f"'{channel_type.value}' were not found"
            )

    def exists(
        self,
        channel_type: GovernanceIntegrityNotificationChannelType,
    ) -> bool:
        return self.get(channel_type) is not None

    @classmethod
    def _secrets_to_parameters(
        cls,
        secrets: GovernanceIntegrityProviderSecrets,
    ) -> dict[str, Any]:
        return {
            "channel_type": secrets.channel_type.value,
            "secrets_json": json.dumps(dict(secrets.values)),
            "updated_at": cls._datetime_to_storage(
                secrets.updated_at
            ),
        }

    @classmethod
    def _row_to_secrets(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityProviderSecrets:
        return GovernanceIntegrityProviderSecrets(
            channel_type=GovernanceIntegrityNotificationChannelType(
                str(row["channel_type"])
            ),
            values=json.loads(str(row["secrets_json"])),
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
