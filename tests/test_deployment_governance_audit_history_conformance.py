from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditAlreadyExistsError,
    GovernanceIntegrityAuditHistoryQuery,
    GovernanceIntegrityAuditHistoryRepository,
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
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


@pytest.fixture(
    params=(
        "in_memory",
        "sqlite",
    )
)
def repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> GovernanceIntegrityAuditHistoryRepository:
    """
    Run every audit history conformance test against every implementation.
    """

    if request.param == "in_memory":
        return InMemoryGovernanceIntegrityAuditHistoryRepository()

    if request.param == "sqlite":
        database = SQLiteDatabase(
            SQLiteDatabaseConfig(
                database_path=(
                    tmp_path / "audit-history-conformance.db"
                ),
            )
        )

        return SQLiteGovernanceIntegrityAuditHistoryRepository(database)

    raise AssertionError(
        "unsupported repository test parameter "
        f"'{request.param}'"
    )


def test_repository_starts_empty(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    assert repository.count() == 0
    assert repository.latest() is None
    assert repository.list() == ()


def test_save_and_get_by_audit_id(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    record = make_record(audit_id="audit-001")

    repository.save(record)

    assert repository.get_by_audit_id("audit-001") == record
    assert repository.count() == 1


def test_get_by_audit_id_returns_none_when_missing(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    assert repository.get_by_audit_id("does-not-exist") is None


def test_duplicate_audit_id_is_rejected(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    record = make_record(audit_id="audit-duplicate")

    repository.save(record)

    with pytest.raises(GovernanceIntegrityAuditAlreadyExistsError):
        repository.save(record)


def test_latest_returns_most_recently_started_audit(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    repository.save(make_record(audit_id="audit-001", offset_minutes=0))
    repository.save(make_record(audit_id="audit-002", offset_minutes=30))
    repository.save(make_record(audit_id="audit-003", offset_minutes=10))

    latest = repository.latest()

    assert latest is not None
    assert latest.audit_id == "audit-002"


def test_list_orders_records_newest_first(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    repository.save(make_record(audit_id="audit-oldest", offset_minutes=0))
    repository.save(make_record(audit_id="audit-newest", offset_minutes=20))
    repository.save(make_record(audit_id="audit-middle", offset_minutes=10))

    assert [record.audit_id for record in repository.list()] == [
        "audit-newest",
        "audit-middle",
        "audit-oldest",
    ]


def test_list_applies_limit(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    for index in range(5):
        repository.save(
            make_record(
                audit_id=f"audit-{index}",
                offset_minutes=index,
            )
        )

    records = repository.list(limit=2)

    assert [record.audit_id for record in records] == [
        "audit-4",
        "audit-3",
    ]


def test_query_filters_by_outcome(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    repository.save(
        make_record(audit_id="audit-healthy", invalid_records=0)
    )

    repository.save(
        make_record(
            audit_id="audit-unhealthy",
            offset_minutes=10,
            invalid_records=2,
        )
    )

    records = repository.query(
        GovernanceIntegrityAuditHistoryQuery(
            outcome=GovernanceIntegrityAuditOutcome.UNHEALTHY
        )
    )

    assert [record.audit_id for record in records] == ["audit-unhealthy"]


def test_query_filters_by_backend(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    repository.save(
        make_record(audit_id="audit-sqlite", backend="sqlite")
    )

    repository.save(
        make_record(
            audit_id="audit-postgres",
            offset_minutes=10,
            backend="postgresql",
        )
    )

    records = repository.query(
        GovernanceIntegrityAuditHistoryQuery(backend="postgresql")
    )

    assert [record.audit_id for record in records] == ["audit-postgres"]


def test_count_reflects_saved_records(
    repository: GovernanceIntegrityAuditHistoryRepository,
) -> None:
    for index in range(3):
        repository.save(
            make_record(
                audit_id=f"audit-{index}",
                offset_minutes=index,
            )
        )

    assert repository.count() == 3
