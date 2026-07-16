from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_bookmarks import (
    InMemoryGovernanceIntegrityAuditBookmarkRepository,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_labels import (
    InMemoryGovernanceIntegrityAuditLabelRepository,
)
from backend.observability.deployment_governance_audit_saved_queries import (
    GovernanceIntegritySavedAuditQuery,
    GovernanceIntegritySavedAuditQueryAlreadyExistsError,
    GovernanceIntegritySavedAuditQueryService,
    InMemoryGovernanceIntegritySavedAuditQueryRepository,
)
from backend.observability.deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchQuery,
    GovernanceIntegrityAuditSearchService,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_audit_saved_queries import (
    SQLiteGovernanceIntegritySavedAuditQueryRepository,
)
from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    healthy: bool = True,
) -> GovernanceIntegrityAuditRecord:
    invalid_records = 0 if healthy else 1

    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if healthy
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


class Harness:
    def __init__(self, *, clock=None) -> None:
        self.history_repository = (
            InMemoryGovernanceIntegrityAuditHistoryRepository()
        )
        self.label_repository = (
            InMemoryGovernanceIntegrityAuditLabelRepository()
        )
        self.bookmark_repository = (
            InMemoryGovernanceIntegrityAuditBookmarkRepository()
        )
        self.saved_query_repository = (
            InMemoryGovernanceIntegritySavedAuditQueryRepository()
        )

        self.search_service = GovernanceIntegrityAuditSearchService(
            self.history_repository,
            self.label_repository,
            self.bookmark_repository,
        )

        self.service = GovernanceIntegritySavedAuditQueryService(
            self.saved_query_repository,
            self.search_service,
            clock=clock,
        )


# --- Model -------------------------------------------------------------


def test_saved_query_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegritySavedAuditQuery(
            name="  ",
            query=GovernanceIntegrityAuditSearchQuery(healthy=True),
            created_at=BASE_TIME,
        )


def test_saved_query_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegritySavedAuditQuery(
            name="healthy",
            query=GovernanceIntegrityAuditSearchQuery(healthy=True),
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_saved_query_to_dict() -> None:
    saved_query = GovernanceIntegritySavedAuditQuery(
        name="healthy",
        query=GovernanceIntegrityAuditSearchQuery(healthy=True),
        created_at=BASE_TIME,
    )

    payload = saved_query.to_dict()

    assert payload["name"] == "healthy"
    assert payload["query"] == {
        "audit_id": None,
        "healthy": True,
        "label": None,
        "bookmark": None,
    }
    assert payload["created_at"] == BASE_TIME.isoformat()


# --- Query round-trip serialization ---------------------------------------


def test_search_query_round_trips_through_dict() -> None:
    query = GovernanceIntegrityAuditSearchQuery(
        audit_id="A", healthy=True, label="release", bookmark="stable"
    )

    restored = GovernanceIntegrityAuditSearchQuery.from_dict(
        query.to_dict()
    )

    assert restored == query


# --- Service ---------------------------------------------------------------


def test_service_saves_query() -> None:
    harness = Harness()

    query = GovernanceIntegrityAuditSearchQuery(healthy=True)

    harness.service.save("healthy", query)

    assert harness.service.get("healthy").name == "healthy"


def test_service_save_rejects_duplicate_name() -> None:
    harness = Harness()

    query = GovernanceIntegrityAuditSearchQuery(healthy=True)

    harness.service.save("healthy", query)

    with pytest.raises(ValueError):
        harness.service.save("healthy", query)


def test_service_execute_matches_direct_search() -> None:
    harness = Harness()

    harness.history_repository.save(
        make_record(audit_id="A", offset_minutes=0, healthy=True)
    )
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10, healthy=False)
    )

    query = GovernanceIntegrityAuditSearchQuery(healthy=True)

    harness.service.save("healthy", query)

    executed = harness.service.execute("healthy")
    direct = harness.search_service.search(query)

    assert executed == direct


def test_service_execute_raises_for_missing_query() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.execute("missing")


def test_service_deletes_query() -> None:
    harness = Harness()

    query = GovernanceIntegrityAuditSearchQuery(healthy=True)

    harness.service.save("healthy", query)

    harness.service.delete("healthy")

    assert harness.saved_query_repository.list() == ()


def test_service_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_service_lists_queries() -> None:
    harness = Harness()

    harness.service.save(
        "healthy", GovernanceIntegrityAuditSearchQuery(healthy=True)
    )
    harness.service.save(
        "baseline", GovernanceIntegrityAuditSearchQuery(label="baseline")
    )

    assert len(harness.service.list()) == 2


def test_service_uses_injected_clock() -> None:
    fixed_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

    harness = Harness(clock=lambda: fixed_time)

    saved_query = harness.service.save(
        "healthy", GovernanceIntegrityAuditSearchQuery(healthy=True)
    )

    assert saved_query.created_at == fixed_time


# --- InMemory repository -------------------------------------------------


def test_in_memory_repository_rejects_duplicate_save() -> None:
    repository = InMemoryGovernanceIntegritySavedAuditQueryRepository()

    saved_query = GovernanceIntegritySavedAuditQuery(
        name="healthy",
        query=GovernanceIntegrityAuditSearchQuery(healthy=True),
        created_at=BASE_TIME,
    )

    repository.save(saved_query)

    with pytest.raises(
        GovernanceIntegritySavedAuditQueryAlreadyExistsError
    ):
        repository.save(saved_query)


# --- SQLite repository -------------------------------------------------


def test_sqlite_repository_save_and_get(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "saved-queries.db",
        )
    )

    repository = SQLiteGovernanceIntegritySavedAuditQueryRepository(
        database
    )

    saved_query = GovernanceIntegritySavedAuditQuery(
        name="healthy",
        query=GovernanceIntegrityAuditSearchQuery(
            healthy=True, label="release"
        ),
        created_at=BASE_TIME,
    )

    repository.save(saved_query)

    retrieved = repository.get("healthy")

    assert retrieved is not None
    assert retrieved.query == saved_query.query


def test_sqlite_repository_rejects_duplicate_save(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "saved-queries-dup.db",
        )
    )

    repository = SQLiteGovernanceIntegritySavedAuditQueryRepository(
        database
    )

    saved_query = GovernanceIntegritySavedAuditQuery(
        name="healthy",
        query=GovernanceIntegrityAuditSearchQuery(healthy=True),
        created_at=BASE_TIME,
    )

    repository.save(saved_query)

    with pytest.raises(
        GovernanceIntegritySavedAuditQueryAlreadyExistsError
    ):
        repository.save(saved_query)


def test_sqlite_repository_delete_missing_raises_key_error(
    tmp_path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "saved-queries-missing.db",
        )
    )

    repository = SQLiteGovernanceIntegritySavedAuditQueryRepository(
        database
    )

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_sqlite_repository_persists_across_runtime_reload(
    tmp_path,
) -> None:
    database_path = tmp_path / "saved-queries-reload.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    runtime.build_integrity_saved_audit_query_service().save(
        "healthy", GovernanceIntegrityAuditSearchQuery(healthy=True)
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    saved_query = (
        reloaded_runtime
        .build_integrity_saved_audit_query_service()
        .get("healthy")
    )

    assert saved_query is not None
    assert saved_query.query == GovernanceIntegrityAuditSearchQuery(
        healthy=True
    )


def test_runtime_builds_working_saved_query_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "saved-query-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_saved_audit_query_service()

    service.save("all-audit-a", GovernanceIntegrityAuditSearchQuery(audit_id="A"))

    results = service.execute("all-audit-a")

    assert len(results) == 1
    assert results[0].audit_id == "A"
