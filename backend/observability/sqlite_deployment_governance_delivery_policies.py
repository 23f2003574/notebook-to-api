from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
    GovernanceIntegrityDeliveryPolicyAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityDeliveryPolicyRepository:
    """
    Durable SQLite implementation of governance audit delivery policy
    storage.

    Conforms structurally to
    GovernanceIntegrityDeliveryPolicyRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityDeliveryPolicyRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "channel_name, retry_limit, timeout_seconds, "
        "rate_limit_per_minute, enabled"
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
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> GovernanceIntegrityDeliveryPolicy:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE}
                    (
                        channel_name,
                        retry_limit,
                        timeout_seconds,
                        rate_limit_per_minute,
                        enabled
                    )
                    VALUES
                    (
                        :channel_name,
                        :retry_limit,
                        :timeout_seconds,
                        :rate_limit_per_minute,
                        :enabled
                    )
                    """,
                    self._policy_to_parameters(policy),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityDeliveryPolicyAlreadyExistsError(
                    "delivery policy for channel "
                    f"'{policy.channel_name}' already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit delivery policy for "
                f"channel '{policy.channel_name}'"
            ) from exc

        return policy

    def get(
        self,
        channel_name: str,
    ) -> GovernanceIntegrityDeliveryPolicy | None:
        normalized_name = self._normalize(channel_name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE}
                WHERE
                    channel_name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit delivery policy "
                f"for channel '{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_policy(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeliveryPolicy,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE}
                ORDER BY
                    channel_name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit delivery policies"
            ) from exc

        return tuple(self._row_to_policy(row) for row in rows)

    def update(
        self,
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> GovernanceIntegrityDeliveryPolicy:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE}
                    SET
                        retry_limit = :retry_limit,
                        timeout_seconds = :timeout_seconds,
                        rate_limit_per_minute = :rate_limit_per_minute,
                        enabled = :enabled
                    WHERE
                        channel_name = :channel_name
                    """,
                    self._policy_to_parameters(policy),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit delivery policy "
                f"for channel '{policy.channel_name}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                "delivery policy for channel "
                f"'{policy.channel_name}' was not found"
            )

        return policy

    def delete(
        self,
        channel_name: str,
    ) -> None:
        normalized_name = self._normalize(channel_name)

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_DELIVERY_POLICY_TABLE}
                    WHERE
                        channel_name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit delivery policy "
                f"for channel '{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"delivery policy for channel '{normalized_name}' "
                "was not found"
            )

    def exists(
        self,
        channel_name: str,
    ) -> bool:
        return self.get(channel_name) is not None

    @staticmethod
    def _normalize(channel_name: str) -> str:
        normalized_name = channel_name.strip()

        if not normalized_name:
            raise ValueError(
                "channel_name must not be empty"
            )

        return normalized_name

    @staticmethod
    def _policy_to_parameters(
        policy: GovernanceIntegrityDeliveryPolicy,
    ) -> dict[str, Any]:
        return {
            "channel_name": policy.channel_name,
            "retry_limit": policy.retry_limit,
            "timeout_seconds": policy.timeout_seconds,
            "rate_limit_per_minute": policy.rate_limit_per_minute,
            "enabled": 1 if policy.enabled else 0,
        }

    @staticmethod
    def _row_to_policy(
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityDeliveryPolicy:
        return GovernanceIntegrityDeliveryPolicy(
            channel_name=str(row["channel_name"]),
            retry_limit=int(row["retry_limit"]),
            timeout_seconds=int(row["timeout_seconds"]),
            rate_limit_per_minute=int(
                row["rate_limit_per_minute"]
            ),
            enabled=bool(row["enabled"]),
        )
