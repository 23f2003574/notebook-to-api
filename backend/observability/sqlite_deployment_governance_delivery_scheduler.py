from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_delivery_scheduler import (
    GovernanceIntegrityDeliveryScheduleAlreadyExistsError,
    GovernanceIntegrityDispatchState,
    GovernanceIntegrityScheduledDispatch,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityDeliveryScheduleRepository:
    """
    Durable SQLite implementation of governance audit delivery
    schedule storage.

    Conforms structurally to
    GovernanceIntegrityDeliveryScheduleRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityDeliveryScheduleRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = "dispatch_id, scheduled_at, state, attempt"

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
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> GovernanceIntegrityScheduledDispatch:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                    (
                        dispatch_id,
                        scheduled_at,
                        state,
                        attempt
                    )
                    VALUES
                    (
                        :dispatch_id,
                        :scheduled_at,
                        :state,
                        :attempt
                    )
                    """,
                    self._scheduled_dispatch_to_parameters(
                        scheduled_dispatch
                    ),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityDeliveryScheduleAlreadyExistsError(
                    "a schedule for dispatch "
                    f"'{scheduled_dispatch.dispatch_id}' already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance audit scheduled dispatch "
                f"'{scheduled_dispatch.dispatch_id}'"
            ) from exc

        return scheduled_dispatch

    def get(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch | None:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                WHERE
                    dispatch_id = ?
                """,
                (str(dispatch_id),),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit scheduled "
                f"dispatch '{dispatch_id}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_scheduled_dispatch(row)

    def update(
        self,
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> GovernanceIntegrityScheduledDispatch:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                    SET
                        scheduled_at = :scheduled_at,
                        state = :state,
                        attempt = :attempt
                    WHERE
                        dispatch_id = :dispatch_id
                    """,
                    self._scheduled_dispatch_to_parameters(
                        scheduled_dispatch
                    ),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit scheduled dispatch "
                f"'{scheduled_dispatch.dispatch_id}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                "no schedule found for dispatch "
                f"'{scheduled_dispatch.dispatch_id}'"
            )

        return scheduled_dispatch

    def delete(
        self,
        dispatch_id: UUID,
    ) -> None:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                    WHERE
                        dispatch_id = ?
                    """,
                    (str(dispatch_id),),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit scheduled dispatch "
                f"'{dispatch_id}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"no schedule found for dispatch '{dispatch_id}'"
            )

    def list_pending(
        self,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        return self._list_by_state_filter(
            f"state = '{GovernanceIntegrityDispatchState.PENDING.value}'"
        )

    def list_ready(
        self,
        now: datetime,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                WHERE
                    state = ?
                    AND scheduled_at <= ?
                ORDER BY
                    scheduled_at ASC, dispatch_id ASC
                """,
                (
                    GovernanceIntegrityDispatchState.PENDING.value,
                    self._datetime_to_storage(now),
                ),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list ready governance audit scheduled "
                "dispatches"
            ) from exc

        return tuple(
            self._row_to_scheduled_dispatch(row) for row in rows
        )

    def _list_by_state_filter(
        self, where_clause: str
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_SCHEDULED_DISPATCH_TABLE}
                WHERE
                    {where_clause}
                ORDER BY
                    scheduled_at ASC, dispatch_id ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit scheduled dispatches"
            ) from exc

        return tuple(
            self._row_to_scheduled_dispatch(row) for row in rows
        )

    @classmethod
    def _scheduled_dispatch_to_parameters(
        cls,
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> dict[str, Any]:
        return {
            "dispatch_id": str(scheduled_dispatch.dispatch_id),
            "scheduled_at": cls._datetime_to_storage(
                scheduled_dispatch.scheduled_at
            ),
            "state": scheduled_dispatch.state.value,
            "attempt": scheduled_dispatch.attempt,
        }

    @classmethod
    def _row_to_scheduled_dispatch(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityScheduledDispatch:
        return GovernanceIntegrityScheduledDispatch(
            dispatch_id=UUID(str(row["dispatch_id"])),
            scheduled_at=cls._datetime_from_storage(
                str(row["scheduled_at"])
            ),
            state=GovernanceIntegrityDispatchState(str(row["state"])),
            attempt=int(row["attempt"]),
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
