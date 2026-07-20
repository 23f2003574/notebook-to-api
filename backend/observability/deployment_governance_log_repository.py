from __future__ import annotations

import json
import sqlite3
from threading import RLock
from typing import Any, Mapping, Protocol, TYPE_CHECKING, runtime_checkable
from datetime import datetime, timezone

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_logging import GovernanceLogEntry
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_LOGS_TABLE,
    DeploymentGovernanceSQLiteSchema,
)

if TYPE_CHECKING:
    from .deployment_governance_log_rotation import (
        GovernanceLogRotationService,
    )

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _validate_level(level: str | None) -> None:
    if level is not None and level not in _VALID_LEVELS:
        raise ValueError(
            f"level must be one of {', '.join(_VALID_LEVELS)}"
        )


@runtime_checkable
class GovernanceLogRepository(Protocol):
    """
    Persistence contract for the append-only history of structured
    governance log entries.
    """

    def append(
        self,
        entry: GovernanceLogEntry,
    ) -> GovernanceLogEntry:
        """
        Add one new log entry to the end of the history. Existing
        entries are never modified.
        """

    def list(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        """
        Return log entries oldest first, optionally filtered to one
        level and/or one component, and optionally capped to the
        first `limit` matching entries.
        """

    def tail(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        """
        Return log entries newest first, optionally filtered to one
        level and/or one component, and optionally capped to the
        most recent `limit` matching entries.
        """

    def clear(self) -> None:
        """
        Discard every stored log entry.
        """

    def prune(self, max_entries: int) -> int:
        """
        Discard the oldest entries beyond max_entries, keeping only
        the most recent ones. Returns the number of entries
        discarded.
        """

    def prune_older_than(self, cutoff: datetime) -> int:
        """
        Discard every entry timestamped strictly before cutoff.
        Returns the number of entries discarded.
        """


class InMemoryGovernanceLogRepository:
    """
    Thread-safe in-memory implementation of governance log storage.
    """

    def __init__(
        self,
        *,
        rotation_service: "GovernanceLogRotationService | None" = None,
    ) -> None:
        self._entries: list[GovernanceLogEntry] = []

        self._lock = RLock()

        self._rotation_service = rotation_service

    def append(
        self,
        entry: GovernanceLogEntry,
    ) -> GovernanceLogEntry:
        with self._lock:
            self._entries.append(entry)

            rotation_service = self._rotation_service

        if rotation_service is not None:
            rotation_service.rotate()

        return entry

    def set_rotation_service(
        self,
        rotation_service: "GovernanceLogRotationService | None",
    ) -> None:
        """
        Attach (or detach) a GovernanceLogRotationService to run
        after every append(), without recreating the repository.
        """

        with self._lock:
            self._rotation_service = rotation_service

    def list(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        _validate_level(level)

        if limit is not None and limit < 0:
            raise ValueError(
                "limit must not be negative"
            )

        with self._lock:
            matches = [
                entry
                for entry in self._entries
                if self._matches(entry, level, component)
            ]

        if limit is not None:
            matches = matches[:limit]

        return tuple(matches)

    def tail(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        _validate_level(level)

        if limit is not None and limit < 0:
            raise ValueError(
                "limit must not be negative"
            )

        with self._lock:
            matches = [
                entry
                for entry in reversed(self._entries)
                if self._matches(entry, level, component)
            ]

        if limit is not None:
            matches = matches[:limit]

        return tuple(matches)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def prune(self, max_entries: int) -> int:
        if max_entries < 0:
            raise ValueError(
                "max_entries must not be negative"
            )

        with self._lock:
            discarded = max(
                0, len(self._entries) - max_entries
            )

            if discarded:
                self._entries = self._entries[discarded:]

            return discarded

    def prune_older_than(self, cutoff: datetime) -> int:
        with self._lock:
            kept = [
                entry
                for entry in self._entries
                if entry.timestamp >= cutoff
            ]

            discarded = len(self._entries) - len(kept)

            self._entries = kept

            return discarded

    @staticmethod
    def _matches(
        entry: GovernanceLogEntry,
        level: str | None,
        component: str | None,
    ) -> bool:
        if level is not None and entry.level != level:
            return False

        if component is not None and entry.component != component:
            return False

        return True


class SQLiteGovernanceLogRepository:
    """
    Durable SQLite implementation of governance log storage.

    Conforms structurally to GovernanceLogRepository so callers can
    swap this repository for InMemoryGovernanceLogRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "id, timestamp, level, component, event, fields_json"
    )

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        initialize_schema: bool = True,
        rotation_service: "GovernanceLogRotationService | None" = None,
    ) -> None:
        self._database = database

        self._rotation_service = rotation_service

        if initialize_schema:
            DeploymentGovernanceSQLiteSchema.initialize(
                self._database
            )

    def append(
        self,
        entry: GovernanceLogEntry,
    ) -> GovernanceLogEntry:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_LOGS_TABLE}
                    (
                        timestamp,
                        level,
                        component,
                        event,
                        fields_json
                    )
                    VALUES
                    (
                        :timestamp,
                        :level,
                        :component,
                        :event,
                        :fields_json
                    )
                    """,
                    self._entry_to_parameters(entry),
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to append governance log entry"
            ) from exc

        if self._rotation_service is not None:
            self._rotation_service.rotate()

        return entry

    def set_rotation_service(
        self,
        rotation_service: "GovernanceLogRotationService | None",
    ) -> None:
        """
        Attach (or detach) a GovernanceLogRotationService to run
        after every append(), without recreating the repository.
        """

        self._rotation_service = rotation_service

    def list(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        return self._query(
            order="id ASC",
            level=level,
            component=component,
            limit=limit,
        )

    def tail(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> tuple[GovernanceLogEntry, ...]:
        return self._query(
            order="id DESC",
            level=level,
            component=component,
            limit=limit,
        )

    def clear(self) -> None:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"DELETE FROM {DEPLOYMENT_GOVERNANCE_LOGS_TABLE}"
                )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to clear governance logs"
            ) from exc

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
                        {DEPLOYMENT_GOVERNANCE_LOGS_TABLE}
                    WHERE
                        id NOT IN (
                            SELECT id FROM
                                {DEPLOYMENT_GOVERNANCE_LOGS_TABLE}
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
                "failed to prune governance logs"
            ) from exc

    def prune_older_than(self, cutoff: datetime) -> int:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    DELETE FROM
                        {DEPLOYMENT_GOVERNANCE_LOGS_TABLE}
                    WHERE
                        timestamp < :cutoff
                    """,
                    {
                        "cutoff": self._datetime_to_storage(
                            cutoff
                        ),
                    },
                )

                return int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to prune governance logs by age"
            ) from exc

    def _query(
        self,
        *,
        order: str,
        level: str | None,
        component: str | None,
        limit: int | None,
    ) -> tuple[GovernanceLogEntry, ...]:
        _validate_level(level)

        if limit is not None and limit < 0:
            raise ValueError(
                "limit must not be negative"
            )

        conditions: list[str] = []

        parameters: list[Any] = []

        if level is not None:
            conditions.append("level = ?")
            parameters.append(level)

        if component is not None:
            conditions.append("component = ?")
            parameters.append(component)

        where_clause = (
            "WHERE " + " AND ".join(conditions)
            if conditions
            else ""
        )

        query = f"""
            SELECT
                {self._SELECT_COLUMNS}
            FROM
                {DEPLOYMENT_GOVERNANCE_LOGS_TABLE}
            {where_clause}
            ORDER BY
                {order}
        """

        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)

        try:
            rows = self._database.query_all(
                query, tuple(parameters)
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance logs"
            ) from exc

        return tuple(self._row_to_entry(row) for row in rows)

    @classmethod
    def _entry_to_parameters(
        cls,
        entry: GovernanceLogEntry,
    ) -> dict[str, Any]:
        return {
            "timestamp": cls._datetime_to_storage(entry.timestamp),
            "level": entry.level,
            "component": entry.component,
            "event": entry.event,
            "fields_json": json.dumps(dict(entry.fields)),
        }

    @classmethod
    def _row_to_entry(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceLogEntry:
        return GovernanceLogEntry(
            timestamp=cls._datetime_from_storage(
                str(row["timestamp"])
            ),
            level=str(row["level"]),
            component=str(row["component"]),
            event=str(row["event"]),
            fields=json.loads(str(row["fields_json"])),
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
