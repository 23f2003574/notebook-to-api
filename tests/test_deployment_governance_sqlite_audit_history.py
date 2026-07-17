from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditAlreadyExistsError,
    GovernanceIntegrityAuditHistoryQuery,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.sqlite_deployment_governance_audit_history import (
    SQLiteGovernanceIntegrityAuditHistoryRepository,
)
from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)


BASE_TIME = datetime(
    2026,
    7,
    15,
    12,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    backend: str = "sqlite",
    invalid_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend=backend,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=3),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=10,
        valid_records=10 - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def make_database(tmp_path: Path, name: str) -> SQLiteDatabase:
    return SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / name,
        )
    )


def test_sqlite_schema_includes_audit_history_migration(
    tmp_path: Path,
) -> None:
    database = make_database(
        tmp_path,
        "audit-history-schema.db",
    )

    SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    assert database.current_schema_version() == 14

    applied_versions = tuple(
        migration.version
        for migration in database.applied_migrations()
    )

    assert applied_versions == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)


def test_sqlite_audit_history_round_trip(
    tmp_path: Path,
) -> None:
    database = make_database(
        tmp_path,
        "audit-history.db",
    )

    repository = SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    record = make_record(audit_id="audit-round-trip")

    repository.save(record)

    restored = repository.get_by_audit_id(record.audit_id)

    assert restored == record
    assert repository.count() == 1


def test_sqlite_audit_history_rejects_duplicate_id(
    tmp_path: Path,
) -> None:
    database = make_database(
        tmp_path,
        "audit-history-duplicate.db",
    )

    repository = SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    record = make_record(audit_id="audit-duplicate")

    repository.save(record)

    with pytest.raises(GovernanceIntegrityAuditAlreadyExistsError):
        repository.save(record)


def test_sqlite_audit_history_lists_newest_first(
    tmp_path: Path,
) -> None:
    database = make_database(
        tmp_path,
        "audit-history-ordering.db",
    )

    repository = SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    repository.save(make_record(audit_id="audit-oldest", offset_minutes=0))
    repository.save(make_record(audit_id="audit-newest", offset_minutes=20))
    repository.save(make_record(audit_id="audit-middle", offset_minutes=10))

    assert [record.audit_id for record in repository.list()] == [
        "audit-newest",
        "audit-middle",
        "audit-oldest",
    ]


def test_sqlite_audit_history_returns_latest_record(
    tmp_path: Path,
) -> None:
    database = make_database(
        tmp_path,
        "audit-history-latest.db",
    )

    repository = SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    repository.save(make_record(audit_id="audit-001", offset_minutes=0))
    repository.save(make_record(audit_id="audit-002", offset_minutes=30))

    latest = repository.latest()

    assert latest is not None
    assert latest.audit_id == "audit-002"


def test_sqlite_audit_history_queries_combined_filters(
    tmp_path: Path,
) -> None:
    database = make_database(
        tmp_path,
        "audit-history-query.db",
    )

    repository = SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    repository.save(
        make_record(
            audit_id="audit-healthy-old",
            offset_minutes=0,
            backend="sqlite",
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="audit-unhealthy-match",
            offset_minutes=10,
            backend="sqlite",
            invalid_records=2,
        )
    )

    repository.save(
        make_record(
            audit_id="audit-unhealthy-other-backend",
            offset_minutes=20,
            backend="postgresql",
            invalid_records=1,
        )
    )

    records = repository.query(
        GovernanceIntegrityAuditHistoryQuery(
            backend="sqlite",
            outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY,
            started_at_or_after=BASE_TIME + timedelta(minutes=5),
        )
    )

    assert [record.audit_id for record in records] == [
        "audit-unhealthy-match"
    ]


def test_sqlite_audit_history_survives_repository_recreation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "durable-audit-history.db"

    first_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    first_repository = SQLiteGovernanceIntegrityAuditHistoryRepository(
        first_database
    )

    record = make_record(audit_id="audit-durable")

    first_repository.save(record)

    del first_repository
    del first_database

    second_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    second_repository = SQLiteGovernanceIntegrityAuditHistoryRepository(
        second_database
    )

    restored = second_repository.get_by_audit_id("audit-durable")

    assert restored == record
    assert second_repository.count() == 1
