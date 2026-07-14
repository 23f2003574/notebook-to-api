from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


class SQLitePersistenceError(RuntimeError):
    """
    Base error for SQLite persistence infrastructure failures.
    """


class SQLiteMigrationError(SQLitePersistenceError):
    """
    Raised when a database migration cannot be safely applied.
    """


class SQLiteMigrationConflictError(SQLiteMigrationError):
    """
    Raised when two migrations attempt to use the same schema version.
    """


@dataclass(frozen=True)
class SQLiteDatabaseConfig:
    """
    Configuration for a notebook2api SQLite database.

    The configuration is intentionally independent of any one repository so
    multiple persistence domains can share the same database infrastructure.
    """

    database_path: Path | str
    timeout_seconds: float = 30.0
    busy_timeout_milliseconds: int = 5000
    enable_foreign_keys: bool = True
    journal_mode: str = "WAL"
    synchronous_mode: str = "NORMAL"

    def __post_init__(self) -> None:
        path = Path(self.database_path).expanduser()

        object.__setattr__(
            self,
            "database_path",
            path,
        )

        if self.timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

        if self.busy_timeout_milliseconds < 0:
            raise ValueError(
                "busy_timeout_milliseconds cannot be negative"
            )

        journal_mode = self.journal_mode.strip().upper()

        allowed_journal_modes = {
            "DELETE",
            "TRUNCATE",
            "PERSIST",
            "MEMORY",
            "WAL",
            "OFF",
        }

        if journal_mode not in allowed_journal_modes:
            raise ValueError(
                "unsupported SQLite journal mode "
                f"'{self.journal_mode}'"
            )

        synchronous_mode = (
            self.synchronous_mode
            .strip()
            .upper()
        )

        allowed_synchronous_modes = {
            "OFF",
            "NORMAL",
            "FULL",
            "EXTRA",
        }

        if synchronous_mode not in allowed_synchronous_modes:
            raise ValueError(
                "unsupported SQLite synchronous mode "
                f"'{self.synchronous_mode}'"
            )

        object.__setattr__(
            self,
            "journal_mode",
            journal_mode,
        )

        object.__setattr__(
            self,
            "synchronous_mode",
            synchronous_mode,
        )


@dataclass(frozen=True)
class SQLiteMigration:
    """
    One ordered SQLite schema migration.

    Migration versions are monotonically increasing integers. Once a migration
    has been released and applied, its SQL should be treated as immutable.
    """

    version: int
    name: str
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise ValueError(
                "migration version must be greater than zero"
            )

        if not self.name.strip():
            raise ValueError(
                "migration name cannot be empty"
            )

        if not self.statements:
            raise ValueError(
                "migration must contain at least one SQL statement"
            )

        normalized_statements = tuple(
            statement.strip()
            for statement in self.statements
            if statement.strip()
        )

        if not normalized_statements:
            raise ValueError(
                "migration must contain at least one non-empty SQL statement"
            )

        object.__setattr__(
            self,
            "statements",
            normalized_statements,
        )


@dataclass(frozen=True)
class AppliedSQLiteMigration:
    """
    Metadata describing a migration already recorded in the database.
    """

    version: int
    name: str
    applied_at: str


class SQLiteDatabase:
    """
    Shared SQLite persistence foundation for notebook2api.

    Responsibilities:

    - database path preparation,
    - connection creation,
    - connection-level SQLite configuration,
    - transaction management,
    - migration metadata,
    - ordered migration execution.

    Domain repositories should depend on this infrastructure rather than
    independently configuring sqlite3 connections.
    """

    MIGRATION_TABLE = "notebook2api_schema_migrations"

    def __init__(
        self,
        config: SQLiteDatabaseConfig,
    ) -> None:
        self._config = config
        self._initialization_lock = RLock()
        self._initialized = False

    @property
    def config(
        self,
    ) -> SQLiteDatabaseConfig:
        return self._config

    @property
    def database_path(
        self,
    ) -> Path:
        return self._config.database_path

    def initialize(
        self,
        migrations: Sequence[SQLiteMigration] = (),
    ) -> None:
        """
        Prepare the database and apply all pending migrations.

        Initialization is idempotent for one SQLiteDatabase instance.
        """

        with self._initialization_lock:
            self._prepare_database_directory()

            self._ensure_migration_table()

            if migrations:
                self.apply_migrations(
                    migrations
                )

            self._initialized = True

    def connect(
        self,
    ) -> sqlite3.Connection:
        """
        Open and configure a new SQLite connection.

        Connections are intentionally short-lived and should normally be used
        through connection() or transaction().
        """

        self._prepare_database_directory()

        try:
            connection = sqlite3.connect(
                str(self.database_path),
                timeout=self._config.timeout_seconds,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to open SQLite database "
                f"'{self.database_path}'"
            ) from exc

        connection.row_factory = sqlite3.Row

        try:
            self._configure_connection(
                connection
            )
        except Exception:
            connection.close()
            raise

        return connection

    @contextmanager
    def connection(
        self,
    ) -> Iterator[sqlite3.Connection]:
        """
        Provide a configured connection without automatically opening an
        explicit transaction.
        """

        connection = self.connect()

        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(
        self,
        *,
        immediate: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        """
        Execute work inside an explicit transaction.

        immediate=True uses BEGIN IMMEDIATE, acquiring the write reservation
        earlier and reducing certain read-then-write race windows.
        """

        connection = self.connect()

        begin_statement = (
            "BEGIN IMMEDIATE"
            if immediate
            else "BEGIN"
        )

        try:
            connection.execute(
                begin_statement
            )

            yield connection

            connection.commit()

        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass

            raise

        finally:
            connection.close()

    def execute(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> int:
        """
        Execute one statement inside a transaction.

        Returns the affected row count when SQLite provides one.
        """

        with self.transaction(
            immediate=True
        ) as connection:
            cursor = connection.execute(
                sql,
                tuple(parameters),
            )

            return cursor.rowcount

    def query_all(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> tuple[sqlite3.Row, ...]:
        """
        Execute a read query and return all rows.
        """

        with self.connection() as connection:
            cursor = connection.execute(
                sql,
                tuple(parameters),
            )

            return tuple(
                cursor.fetchall()
            )

    def query_one(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> sqlite3.Row | None:
        """
        Execute a read query and return at most one row.
        """

        with self.connection() as connection:
            cursor = connection.execute(
                sql,
                tuple(parameters),
            )

            return cursor.fetchone()

    def apply_migrations(
        self,
        migrations: Sequence[SQLiteMigration],
    ) -> tuple[int, ...]:
        """
        Apply pending migrations in ascending version order.

        Returns the versions applied during this invocation.
        """

        ordered_migrations = self._validate_migrations(
            migrations
        )

        self._ensure_migration_table()

        applied_versions = {
            migration.version
            for migration in self.applied_migrations()
        }

        newly_applied_versions: list[int] = []

        for migration in ordered_migrations:
            if migration.version in applied_versions:
                continue

            self._apply_migration(
                migration
            )

            newly_applied_versions.append(
                migration.version
            )

        return tuple(
            newly_applied_versions
        )

    def applied_migrations(
        self,
    ) -> tuple[AppliedSQLiteMigration, ...]:
        """
        Return migrations already recorded in the database.
        """

        self._ensure_migration_table()

        rows = self.query_all(
            f"""
            SELECT
                version,
                name,
                applied_at
            FROM {self.MIGRATION_TABLE}
            ORDER BY version ASC
            """
        )

        return tuple(
            AppliedSQLiteMigration(
                version=int(row["version"]),
                name=str(row["name"]),
                applied_at=str(row["applied_at"]),
            )
            for row in rows
        )

    def current_schema_version(
        self,
    ) -> int:
        """
        Return the highest applied migration version.

        An unversioned database reports version 0.
        """

        self._ensure_migration_table()

        row = self.query_one(
            f"""
            SELECT
                COALESCE(MAX(version), 0) AS version
            FROM {self.MIGRATION_TABLE}
            """
        )

        if row is None:
            return 0

        return int(
            row["version"]
        )

    def _prepare_database_directory(
        self,
    ) -> None:
        parent = self.database_path.parent

        try:
            parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise SQLitePersistenceError(
                "failed to create SQLite database directory "
                f"'{parent}'"
            ) from exc

    def _configure_connection(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """
        Apply notebook2api's SQLite connection policy.
        """

        try:
            connection.execute(
                "PRAGMA busy_timeout = "
                f"{self._config.busy_timeout_milliseconds}"
            )

            if self._config.enable_foreign_keys:
                connection.execute(
                    "PRAGMA foreign_keys = ON"
                )
            else:
                connection.execute(
                    "PRAGMA foreign_keys = OFF"
                )

            connection.execute(
                "PRAGMA journal_mode = "
                f"{self._config.journal_mode}"
            )

            connection.execute(
                "PRAGMA synchronous = "
                f"{self._config.synchronous_mode}"
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to configure SQLite connection"
            ) from exc

    def _ensure_migration_table(
        self,
    ) -> None:
        self._prepare_database_directory()

        with self.connection() as connection:
            try:
                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS
                    {self.MIGRATION_TABLE}
                    (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                            DEFAULT (
                                strftime(
                                    '%Y-%m-%dT%H:%M:%fZ',
                                    'now'
                                )
                            )
                    )
                    """
                )
            except sqlite3.Error as exc:
                raise SQLitePersistenceError(
                    "failed to initialize SQLite migration metadata"
                ) from exc

    def _apply_migration(
        self,
        migration: SQLiteMigration,
    ) -> None:
        try:
            with self.transaction(
                immediate=True
            ) as connection:

                existing = connection.execute(
                    f"""
                    SELECT
                        version,
                        name
                    FROM {self.MIGRATION_TABLE}
                    WHERE version = ?
                    """,
                    (
                        migration.version,
                    ),
                ).fetchone()

                if existing is not None:
                    return

                for statement in migration.statements:
                    connection.execute(
                        statement
                    )

                connection.execute(
                    f"""
                    INSERT INTO {self.MIGRATION_TABLE}
                    (
                        version,
                        name
                    )
                    VALUES (?, ?)
                    """,
                    (
                        migration.version,
                        migration.name,
                    ),
                )

        except sqlite3.Error as exc:
            raise SQLiteMigrationError(
                "failed to apply SQLite migration "
                f"{migration.version}: {migration.name}"
            ) from exc

    @staticmethod
    def _validate_migrations(
        migrations: Sequence[SQLiteMigration],
    ) -> tuple[SQLiteMigration, ...]:
        migrations = tuple(
            migrations
        )

        seen_versions: dict[
            int,
            SQLiteMigration,
        ] = {}

        for migration in migrations:
            existing = seen_versions.get(
                migration.version
            )

            if existing is not None:
                raise SQLiteMigrationConflictError(
                    "multiple SQLite migrations use version "
                    f"{migration.version}: "
                    f"'{existing.name}' and '{migration.name}'"
                )

            seen_versions[
                migration.version
            ] = migration

        return tuple(
            sorted(
                migrations,
                key=lambda migration: migration.version,
            )
        )
