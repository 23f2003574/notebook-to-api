from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLitePersistenceError,
)

from .deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportSource,
    GovernanceIntegrityAuditReportTemplate,
    GovernanceIntegrityAuditReportTemplateAlreadyExistsError,
)
from .sqlite_deployment_governance_schema import (
    DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE,
    DeploymentGovernanceSQLiteSchema,
)


class SQLiteGovernanceIntegrityAuditReportTemplateRepository:
    """
    Durable SQLite implementation of governance audit report template
    storage.

    Conforms structurally to
    GovernanceIntegrityAuditReportTemplateRepository so callers can swap
    this repository for
    InMemoryGovernanceIntegrityAuditReportTemplateRepository without
    observing different behavior.
    """

    _SELECT_COLUMNS = (
        "name, title, source, source_name, output_format, created_at"
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
        template: GovernanceIntegrityAuditReportTemplate,
    ) -> GovernanceIntegrityAuditReportTemplate:
        try:
            with self._database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    f"""
                    INSERT INTO
                    {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE}
                    (
                        name,
                        title,
                        source,
                        source_name,
                        output_format,
                        created_at
                    )
                    VALUES
                    (
                        :name,
                        :title,
                        :source,
                        :source_name,
                        :output_format,
                        :created_at
                    )
                    """,
                    {
                        "name": template.name,
                        "title": template.title,
                        "source": template.source.value,
                        "source_name": template.source_name,
                        "output_format": template.output_format,
                        "created_at": self._datetime_to_storage(
                            template.created_at
                        ),
                    },
                )

        except sqlite3.IntegrityError as exc:
            raise (
                GovernanceIntegrityAuditReportTemplateAlreadyExistsError(
                    f"report template '{template.name}' already exists"
                )
            ) from exc

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"failed to save governance audit report template "
                f"'{template.name}'"
            ) from exc

        return template

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityAuditReportTemplate | None:
        normalized_name = self._normalize_name(name)

        try:
            row = self._database.query_one(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE}
                WHERE
                    name = ?
                """,
                (normalized_name,),
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to retrieve governance audit report template "
                f"'{normalized_name}'"
            ) from exc

        if row is None:
            return None

        return self._row_to_template(row)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditReportTemplate,
        ...
    ]:
        try:
            rows = self._database.query_all(
                f"""
                SELECT
                    {self._SELECT_COLUMNS}
                FROM
                    {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE}
                ORDER BY
                    name ASC
                """
            )

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to list governance audit report templates"
            ) from exc

        return tuple(self._row_to_template(row) for row in rows)

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
                        {DEPLOYMENT_GOVERNANCE_AUDIT_REPORT_TEMPLATE_TABLE}
                    WHERE
                        name = ?
                    """,
                    (normalized_name,),
                )

                deleted = int(cursor.rowcount)

        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                "failed to delete governance audit report template "
                f"'{normalized_name}'"
            ) from exc

        if deleted == 0:
            raise KeyError(
                f"report template '{normalized_name}' was not found"
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

    @classmethod
    def _row_to_template(
        cls,
        row: Mapping[str, Any],
    ) -> GovernanceIntegrityAuditReportTemplate:
        return GovernanceIntegrityAuditReportTemplate(
            name=str(row["name"]),
            title=str(row["title"]),
            source=GovernanceIntegrityAuditReportSource(
                row["source"]
            ),
            source_name=str(row["source_name"]),
            output_format=str(row["output_format"]),
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
