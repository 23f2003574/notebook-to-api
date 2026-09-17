from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping, Protocol, runtime_checkable

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_metrics import GovernanceIntegrityMetrics
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_METRICS_TABLE,
    DeploymentGovernanceSQLiteSchema,
)

_METRICS_ROW_ID = 1


@runtime_checkable
class GovernanceIntegrityMetricsRepository(Protocol):
    """
    Persistence contract for the single durable snapshot of live
    governance audit notification delivery metrics.

    Unlike most governance audit repositories, this stores exactly
    one row: metrics are a running singleton, not a history of
    records.
    """

    def load(self) -> GovernanceIntegrityMetrics | None:
        """
        Return the persisted metrics snapshot, or None if nothing has
        been saved yet.
        """

    def save(
        self,
        metrics: GovernanceIntegrityMetrics,
    ) -> GovernanceIntegrityMetrics:
        """
        Persist a metrics snapshot, replacing whatever was stored
        before.
        """

    def reset(self) -> None:
        """
        Clear the persisted metrics snapshot entirely.
        """


class InMemoryGovernanceIntegrityMetricsRepository:
    """
    Thread-safe in-memory implementation of governance metrics
    storage.
    """

    def __init__(self) -> None:
        self._metrics: GovernanceIntegrityMetrics | None = None

        self._lock = RLock()

    def load(self) -> GovernanceIntegrityMetrics | None:
        with self._lock:
            return self._metrics

    def save(
        self,
        metrics: GovernanceIntegrityMetrics,
    ) -> GovernanceIntegrityMetrics:
        with self._lock:
            self._metrics = metrics

            return metrics

    def reset(self) -> None:
        with self._lock:
            self._metrics = None


class SQLiteGovernanceIntegrityMetricsRepository:
    """
    Durable SQLite implementation of governance metrics storage.

    Conforms structurally to GovernanceIntegrityMetricsRepository so
    callers can swap this repository for
    InMemoryGovernanceIntegrityMetricsRepository without observing
    different behavior.
    """

    _SELECT_COLUMNS = "metrics_json, updated_at"

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

    def load(self) -> GovernanceIntegrityMetrics | None:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_METRICS_TABLE}
                WHERE
                    id = ?
                """,
                (_METRICS_ROW_ID,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance metrics"
            ) from exc

        if row is None:
            return None

        return self._row_to_metrics(row)

    def save(
        self,
        metrics: GovernanceIntegrityMetrics,
    ) -> GovernanceIntegrityMetrics:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_METRICS_TABLE}
                    (
                        id,
                        metrics_json,
                        updated_at
                    )
                    VALUES
                    (
                        :id,
                        :metrics_json,
                        :updated_at
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        metrics_json = excluded.metrics_json,
                        updated_at = excluded.updated_at
                    """,
                    self._metrics_to_parameters(metrics),
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to save governance metrics"
            ) from exc

        return metrics

    def reset(self) -> None:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_METRICS_TABLE}
                    WHERE
                        id = ?
                    """,
                    (_METRICS_ROW_ID,),
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to reset governance metrics"
            ) from exc

    @classmethod
    def _metrics_to_parameters(
        cls,
        metrics: GovernanceIntegrityMetrics,
    ) -> dict[str, Any]:
        return {
            "id": _METRICS_ROW_ID,
            "metrics_json": json.dumps(metrics.to_dict()),
            "updated_at": cls._datetime_to_storage(
                datetime.now(timezone.utc)
            ),
        }

    @staticmethod
    def _row_to_metrics(
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityMetrics:
        payload = json.loads(str(row["metrics_json"]))

        return GovernanceIntegrityMetrics(
            total_dispatches=int(payload["total_dispatches"]),
            successful_dispatches=int(
                payload["successful_dispatches"]
            ),
            failed_dispatches=int(payload["failed_dispatches"]),
            retry_dispatches=int(payload["retry_dispatches"]),
            average_duration_ms=float(
                payload["average_duration_ms"]
            ),
        )

    @staticmethod
    def _datetime_to_storage(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        return value.isoformat()
