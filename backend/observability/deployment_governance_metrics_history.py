from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping, Protocol, runtime_checkable

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_metrics import GovernanceIntegrityMetrics
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


@dataclass(frozen=True)
class GovernanceIntegrityMetricsSnapshot:
    """
    One immutable, point-in-time capture of governance audit
    notification delivery metrics, taken for trend analysis.

    Unlike the single live GovernanceIntegrityMetrics counters, a
    snapshot never changes once captured: it is a historical record,
    not a running total.
    """

    captured_at: datetime

    metrics: GovernanceIntegrityMetrics

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError(
                "captured_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "captured_at": self.captured_at.isoformat(),
            "metrics": self.metrics.to_dict(),
        }


@runtime_checkable
class GovernanceIntegrityMetricsHistoryRepository(Protocol):
    """
    Persistence contract for the append-only history of periodic
    governance metrics snapshots.
    """

    def append(
        self,
        snapshot: GovernanceIntegrityMetricsSnapshot,
    ) -> GovernanceIntegrityMetricsSnapshot:
        """
        Add one new snapshot to the end of the history. Existing
        entries are never modified.
        """

    def latest(self) -> GovernanceIntegrityMetricsSnapshot | None:
        """
        Return the most recently captured snapshot, or None if the
        history is empty.
        """

    def list(
        self,
        limit: int | None = None,
    ) -> tuple[GovernanceIntegrityMetricsSnapshot, ...]:
        """
        Return captured snapshots newest first, optionally capped to
        the most recent `limit` entries.
        """

    def prune(self, max_entries: int) -> int:
        """
        Discard the oldest snapshots beyond `max_entries`, keeping
        only the most recent ones. Returns the number of snapshots
        discarded.
        """


class InMemoryGovernanceIntegrityMetricsHistoryRepository:
    """
    Thread-safe in-memory implementation of governance metrics
    history storage.
    """

    def __init__(self) -> None:
        self._snapshots: list[GovernanceIntegrityMetricsSnapshot] = []

        self._lock = RLock()

    def append(
        self,
        snapshot: GovernanceIntegrityMetricsSnapshot,
    ) -> GovernanceIntegrityMetricsSnapshot:
        with self._lock:
            self._snapshots.append(snapshot)

            return snapshot

    def latest(self) -> GovernanceIntegrityMetricsSnapshot | None:
        with self._lock:
            if not self._snapshots:
                return None

            return self._snapshots[-1]

    def list(
        self,
        limit: int | None = None,
    ) -> tuple[GovernanceIntegrityMetricsSnapshot, ...]:
        with self._lock:
            newest_first = tuple(reversed(self._snapshots))

        if limit is None:
            return newest_first

        if limit < 0:
            raise ValueError(
                "limit must not be negative"
            )

        return newest_first[:limit]

    def prune(self, max_entries: int) -> int:
        if max_entries < 0:
            raise ValueError(
                "max_entries must not be negative"
            )

        with self._lock:
            discarded = max(
                0, len(self._snapshots) - max_entries
            )

            if discarded:
                self._snapshots = self._snapshots[discarded:]

            return discarded


class SQLiteGovernanceIntegrityMetricsHistoryRepository:
    """
    Durable SQLite implementation of governance metrics history
    storage.

    Conforms structurally to
    GovernanceIntegrityMetricsHistoryRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityMetricsHistoryRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = "id, captured_at, metrics_json"

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

    def append(
        self,
        snapshot: GovernanceIntegrityMetricsSnapshot,
    ) -> GovernanceIntegrityMetricsSnapshot:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
                    (
                        captured_at,
                        metrics_json
                    )
                    VALUES
                    (
                        :captured_at,
                        :metrics_json
                    )
                    """,
                    self._snapshot_to_parameters(snapshot),
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to append governance metrics snapshot"
            ) from exc

        return snapshot

    def latest(self) -> GovernanceIntegrityMetricsSnapshot | None:
        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
                ORDER BY
                    id DESC
                LIMIT 1
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve latest governance metrics "
                "snapshot"
            ) from exc

        if row is None:
            return None

        return self._row_to_snapshot(row)

    def list(
        self,
        limit: int | None = None,
    ) -> tuple[GovernanceIntegrityMetricsSnapshot, ...]:
        if limit is not None and limit < 0:
            raise ValueError(
                "limit must not be negative"
            )

        query = (
            f"""
            SELECT
                {self._SELECT_COLUMNS}
            FROM
                {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
            ORDER BY
                id DESC
            """
        )

        parameters: tuple[Any, ...] = ()

        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)

        try:
            rows = self._database.query_all(query, parameters)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance metrics history"
            ) from exc

        return tuple(self._row_to_snapshot(row) for row in rows)

    def prune(self, max_entries: int) -> int:
        if max_entries < 0:
            raise ValueError(
                "max_entries must not be negative"
            )

        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
                    WHERE
                        id NOT IN (
                            SELECT id FROM
                                {DEPLOYMENT_GOVERNANCE_METRICS_HISTORY_TABLE}
                            ORDER BY
                                id DESC
                            LIMIT ?
                        )
                    """,
                    (max_entries,),
                )

                return int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to prune governance metrics history"
            ) from exc

    @classmethod
    def _snapshot_to_parameters(
        cls,
        snapshot: GovernanceIntegrityMetricsSnapshot,
    ) -> dict[str, Any]:
        return {
            "captured_at": cls._datetime_to_storage(
                snapshot.captured_at
            ),
            "metrics_json": json.dumps(snapshot.metrics.to_dict()),
        }

    @classmethod
    def _row_to_snapshot(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityMetricsSnapshot:
        payload = json.loads(str(row["metrics_json"]))

        return GovernanceIntegrityMetricsSnapshot(
            captured_at=cls._datetime_from_storage(
                str(row["captured_at"])
            ),
            metrics=GovernanceIntegrityMetrics(
                total_dispatches=int(payload["total_dispatches"]),
                successful_dispatches=int(
                    payload["successful_dispatches"]
                ),
                failed_dispatches=int(payload["failed_dispatches"]),
                retry_dispatches=int(payload["retry_dispatches"]),
                average_duration_ms=float(
                    payload["average_duration_ms"]
                ),
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
