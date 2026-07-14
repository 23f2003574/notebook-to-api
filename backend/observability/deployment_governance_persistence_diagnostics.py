from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .deployment_governance_integrity_audit import (
    GovernanceTraceIntegrityAuditReport,
)
from .deployment_governance_persistence import (
    DeploymentGovernancePersistenceBackend,
    DeploymentGovernancePersistenceRuntime,
)


@dataclass(frozen=True)
class GovernancePersistenceSchemaDiagnostics:
    """
    Schema and migration metadata for a durable governance persistence
    backend.

    Non-durable backends do not expose this structure.
    """

    current_version: int

    applied_versions: tuple[int, ...]

    migration_count: int

    @property
    def initialized(
        self,
    ) -> bool:
        return (
            self.current_version
            > 0
        )


@dataclass(frozen=True)
class GovernancePersistenceRepositoryDiagnostics:
    """
    Repository-level governance persistence statistics.
    """

    total_records: int

    statistics: Mapping[str, Any]


@dataclass(frozen=True)
class GovernancePersistenceIntegrityDiagnostics:
    """
    Integrity-audit diagnostics for the active persistence backend.
    """

    supported: bool

    executed: bool

    healthy: bool | None

    total_records: int | None

    valid_records: int | None

    invalid_records: int | None

    integrity_mismatches: int | None

    missing_integrity_metadata: int | None

    invalid_integrity_metadata: int | None

    invalid_persisted_records: int | None

    audit_started_at: datetime | None

    audit_completed_at: datetime | None

    @classmethod
    def unsupported(
        cls,
    ) -> "GovernancePersistenceIntegrityDiagnostics":
        return cls(
            supported=False,
            executed=False,
            healthy=None,
            total_records=None,
            valid_records=None,
            invalid_records=None,
            integrity_mismatches=None,
            missing_integrity_metadata=None,
            invalid_integrity_metadata=None,
            invalid_persisted_records=None,
            audit_started_at=None,
            audit_completed_at=None,
        )

    @classmethod
    def supported_not_executed(
        cls,
    ) -> "GovernancePersistenceIntegrityDiagnostics":
        return cls(
            supported=True,
            executed=False,
            healthy=None,
            total_records=None,
            valid_records=None,
            invalid_records=None,
            integrity_mismatches=None,
            missing_integrity_metadata=None,
            invalid_integrity_metadata=None,
            invalid_persisted_records=None,
            audit_started_at=None,
            audit_completed_at=None,
        )

    @classmethod
    def from_report(
        cls,
        report: GovernanceTraceIntegrityAuditReport,
    ) -> "GovernancePersistenceIntegrityDiagnostics":
        return cls(
            supported=True,
            executed=True,
            healthy=report.healthy,
            total_records=report.total_records,
            valid_records=report.valid_records,
            invalid_records=report.invalid_records,
            integrity_mismatches=(
                report.integrity_mismatches
            ),
            missing_integrity_metadata=(
                report.missing_integrity_metadata
            ),
            invalid_integrity_metadata=(
                report.invalid_integrity_metadata
            ),
            invalid_persisted_records=(
                report.invalid_persisted_records
            ),
            audit_started_at=(
                report.started_at
            ),
            audit_completed_at=(
                report.completed_at
            ),
        )


@dataclass(frozen=True)
class GovernancePersistenceDiagnosticsSnapshot:
    """
    Immutable operational snapshot of deployment governance persistence.
    """

    captured_at: datetime

    backend: DeploymentGovernancePersistenceBackend

    durable: bool

    database_path: Path | None

    schema: GovernancePersistenceSchemaDiagnostics | None

    repository: GovernancePersistenceRepositoryDiagnostics

    integrity: GovernancePersistenceIntegrityDiagnostics

    @property
    def operationally_healthy(
        self,
    ) -> bool:
        """
        Return the strongest health conclusion available from this snapshot.

        A backend without integrity auditing is considered operationally
        healthy when repository diagnostics were captured successfully.

        A backend with an executed integrity audit is healthy only when that
        audit passed.

        A supported but non-executed audit remains operationally healthy but
        does not imply verified persistence integrity.
        """

        if (
            self.integrity.executed
            and self.integrity.healthy is False
        ):
            return False

        return True

    @property
    def integrity_verified(
        self,
    ) -> bool:
        """
        Return whether a successful integrity audit was executed.
        """

        return (
            self.integrity.executed
            and self.integrity.healthy is True
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        """
        Serialize the diagnostics snapshot into JSON-compatible primitives.
        """

        return {
            "captured_at": (
                self.captured_at.isoformat()
            ),
            "backend": self.backend.value,
            "durable": self.durable,
            "database_path": (
                None
                if self.database_path is None
                else str(
                    self.database_path
                )
            ),
            "schema": (
                None
                if self.schema is None
                else {
                    "current_version": (
                        self.schema.current_version
                    ),
                    "applied_versions": list(
                        self.schema.applied_versions
                    ),
                    "migration_count": (
                        self.schema.migration_count
                    ),
                    "initialized": (
                        self.schema.initialized
                    ),
                }
            ),
            "repository": {
                "total_records": (
                    self.repository.total_records
                ),
                "statistics": dict(
                    self.repository.statistics
                ),
            },
            "integrity": {
                "supported": (
                    self.integrity.supported
                ),
                "executed": (
                    self.integrity.executed
                ),
                "healthy": (
                    self.integrity.healthy
                ),
                "total_records": (
                    self.integrity.total_records
                ),
                "valid_records": (
                    self.integrity.valid_records
                ),
                "invalid_records": (
                    self.integrity.invalid_records
                ),
                "integrity_mismatches": (
                    self.integrity.integrity_mismatches
                ),
                "missing_integrity_metadata": (
                    self.integrity
                    .missing_integrity_metadata
                ),
                "invalid_integrity_metadata": (
                    self.integrity
                    .invalid_integrity_metadata
                ),
                "invalid_persisted_records": (
                    self.integrity
                    .invalid_persisted_records
                ),
                "audit_started_at": (
                    None
                    if (
                        self.integrity.audit_started_at
                        is None
                    )
                    else (
                        self.integrity
                        .audit_started_at
                        .isoformat()
                    )
                ),
                "audit_completed_at": (
                    None
                    if (
                        self.integrity.audit_completed_at
                        is None
                    )
                    else (
                        self.integrity
                        .audit_completed_at
                        .isoformat()
                    )
                ),
            },
            "operationally_healthy": (
                self.operationally_healthy
            ),
            "integrity_verified": (
                self.integrity_verified
            ),
        }


class DeploymentGovernancePersistenceDiagnosticsService:
    """
    Collects a unified operational snapshot for governance persistence.
    """

    def __init__(
        self,
        runtime: DeploymentGovernancePersistenceRuntime,
    ) -> None:
        self._runtime = runtime

    def capture(
        self,
        *,
        include_integrity_audit: bool = False,
        integrity_audit_batch_size: int = 500,
    ) -> GovernancePersistenceDiagnosticsSnapshot:
        """
        Capture a point-in-time governance persistence diagnostics snapshot.
        """

        if integrity_audit_batch_size <= 0:
            raise ValueError(
                "integrity_audit_batch_size must be greater than zero"
            )

        repository_diagnostics = (
            self._capture_repository_diagnostics()
        )

        schema_diagnostics = (
            self._capture_schema_diagnostics()
        )

        integrity_diagnostics = (
            self._capture_integrity_diagnostics(
                include_integrity_audit=(
                    include_integrity_audit
                ),
                batch_size=(
                    integrity_audit_batch_size
                ),
            )
        )

        return GovernancePersistenceDiagnosticsSnapshot(
            captured_at=datetime.now(
                timezone.utc
            ),
            backend=self._runtime.backend,
            durable=self._runtime.durable,
            database_path=self._database_path(),
            schema=schema_diagnostics,
            repository=repository_diagnostics,
            integrity=integrity_diagnostics,
        )

    def _capture_repository_diagnostics(
        self,
    ) -> GovernancePersistenceRepositoryDiagnostics:
        """
        Capture repository-level statistics.

        The repository contract's statistics() returns a
        GovernanceTraceRepositoryStatistics dataclass rather than a mapping,
        so it is normalized here via dataclasses.asdict() rather than a bare
        dict() call, which would fail on a non-mapping object.
        """

        total_records = (
            self._runtime.repository.count()
        )

        statistics = (
            self._runtime.repository.statistics()
        )

        statistics_mapping = (
            asdict(statistics)
            if is_dataclass(statistics)
            else dict(statistics)
        )

        return GovernancePersistenceRepositoryDiagnostics(
            total_records=total_records,
            statistics=statistics_mapping,
        )

    def _capture_schema_diagnostics(
        self,
    ) -> GovernancePersistenceSchemaDiagnostics | None:
        """
        Capture schema metadata when the active runtime exposes a database.

        The SQLite persistence foundation (Commit #5) exposes migration
        history via applied_migrations(), returning AppliedSQLiteMigration
        objects rather than bare integers, so versions are extracted here.
        """

        database = self._runtime.database

        if database is None:
            return None

        current_version = (
            database.current_schema_version()
        )

        applied_versions = tuple(
            migration.version
            for migration in database.applied_migrations()
        )

        return GovernancePersistenceSchemaDiagnostics(
            current_version=current_version,
            applied_versions=applied_versions,
            migration_count=len(
                applied_versions
            ),
        )

    def _capture_integrity_diagnostics(
        self,
        *,
        include_integrity_audit: bool,
        batch_size: int,
    ) -> GovernancePersistenceIntegrityDiagnostics:
        """
        Capture integrity capability metadata and optionally execute an audit.
        """

        if not self._runtime.supports_integrity_audit:
            return (
                GovernancePersistenceIntegrityDiagnostics
                .unsupported()
            )

        if not include_integrity_audit:
            return (
                GovernancePersistenceIntegrityDiagnostics
                .supported_not_executed()
            )

        report = (
            self._runtime
            .build_integrity_audit_service()
            .audit(
                batch_size=batch_size
            )
        )

        return (
            GovernancePersistenceIntegrityDiagnostics
            .from_report(
                report
            )
        )

    def _database_path(
        self,
    ) -> Path | None:
        """
        Return the configured durable database path when applicable.
        """

        if (
            self._runtime.backend
            is not DeploymentGovernancePersistenceBackend.SQLITE
        ):
            return None

        return self._runtime.config.database_path
