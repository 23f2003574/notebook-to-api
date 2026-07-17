from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_failure_policy import (
    GovernanceIntegrityFailureAction,
    GovernanceIntegrityFailurePolicy,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityFailurePolicyRepository:
    """
    Durable SQLite implementation of governance audit failure policy
    storage.

    Conforms structurally to GovernanceIntegrityFailurePolicyRepository
    so callers can swap this repository for
    InMemoryGovernanceIntegrityFailurePolicyRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = "name, action, max_retry_attempts"

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
        policy: GovernanceIntegrityFailurePolicy,
    ) -> GovernanceIntegrityFailurePolicy:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE}
                    (
                        name,
                        action,
                        max_retry_attempts
                    )
                    VALUES
                    (
                        :name,
                        :action,
                        :max_retry_attempts
                    )
                    ON CONFLICT (name) DO UPDATE SET
                        action = excluded.action,
                        max_retry_attempts = excluded.max_retry_attempts
                    """,
                    {
                        "name": policy.name,
                        "action": policy.action.value,
                        "max_retry_attempts": policy.max_retry_attempts,
                    },
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit failure policy "
                f"'{policy.name}'"
            ) from exc

        return policy

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityFailurePolicy | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit failure policy "
                f"'{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_policy(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityFailurePolicy,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit failure policies"
            ) from exc

        return tuple(self._row_to_policy(row) for row in rows)

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
                        {DEPLOYMENT_GOVERNANCE_FAILURE_POLICY_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit failure policy "
                f"'{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"failure policy '{normalized_name}' was not found"
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

    @staticmethod
    def _row_to_policy(
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityFailurePolicy:
        return GovernanceIntegrityFailurePolicy(
            name=str(row["name"]),
            action=GovernanceIntegrityFailureAction(
                row["action"]
            ),
            max_retry_attempts=int(row["max_retry_attempts"]),
        )
