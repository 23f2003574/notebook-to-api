from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_report_schedule import (
    GovernanceIntegrityAuditReportSchedule,
    GovernanceIntegrityAuditReportScheduleAlreadyExistsError,
    GovernanceIntegrityReportScheduleFrequency,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityAuditReportScheduleRepository:
    """
    Durable SQLite implementation of governance audit report schedule
    storage.

    Conforms structurally to
    GovernanceIntegrityAuditReportScheduleRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityAuditReportScheduleRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "name, template_name, frequency, enabled, created_at"
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
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> GovernanceIntegrityAuditReportSchedule:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE}
                    (
                        name,
                        template_name,
                        frequency,
                        enabled,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :template_name,
                        :frequency,
                        :enabled,
                        :created_at
                    )
                    """,
                    self._schedule_to_parameters(schedule),
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityAuditReportScheduleAlreadyExistsError(
                    f"report schedule '{schedule.name}' already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit report schedule "
                f"'{schedule.name}'"
            ) from exc

        return schedule

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportSchedule | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit report schedule "
                f"'{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_schedule(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportSchedule,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit report schedules"
            ) from exc

        return tuple(self._row_to_schedule(row) for row in rows)

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
                        {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit report schedule "
                f"'{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"report schedule '{normalized_name}' was not found"
            )

    def exists(
        self,
        name: str,
    ) -> bool:
        return self.get(name) is not None

    def update(
        self,
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> GovernanceIntegrityAuditReportSchedule:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                cursor = connection.execute(
                    f"""
                    UPDATE
                        {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_SCHEDULE_TABLE}
                    SET
                        template_name = :template_name,
                        frequency = :frequency,
                        enabled = :enabled,
                        created_at = :created_at
                    WHERE
                        name = :name
                    """,
                    self._schedule_to_parameters(schedule),
                )

                updated = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to update governance audit report schedule "
                f"'{schedule.name}'"
            ) from exc

        if updated == 0:
            raise KeyError(
                f"report schedule '{schedule.name}' was not found"
            )

        return schedule

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name

    @classmethod
    def _schedule_to_parameters(
        cls,
        schedule: GovernanceIntegrityAuditReportSchedule,
    ) -> dict[str, Any]:
        return {
            "name": schedule.name,
            "template_name": schedule.template_name,
            "frequency": schedule.frequency.value,
            "enabled": 1 if schedule.enabled else 0,
            "created_at": cls._datetime_to_storage(
                schedule.created_at
            ),
        }

    @classmethod
    def _row_to_schedule(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityAuditReportSchedule:
        return GovernanceIntegrityAuditReportSchedule(
            name=str(row["name"]),
            template_name=str(row["template_name"]),
            frequency=GovernanceIntegrityReportScheduleFrequency(
                row["frequency"]
            ),
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
