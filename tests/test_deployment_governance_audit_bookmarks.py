from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_bookmarks import (
    GovernanceIntegrityAuditBookmark,
    GovernanceIntegrityAuditBookmarkAlreadyExistsError,
    GovernanceIntegrityAuditBookmarkService,
    InMemoryGovernanceIntegrityAuditBookmarkRepository,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_audit_bookmarks import (
    SQLiteGovernanceIntegrityAuditBookmarkRepository,
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


def make_service(
    history_repository: InMemoryGovernanceIntegrityAuditHistoryRepository,
    *,
    clock=None,
) -> GovernanceIntegrityAuditBookmarkService:
    return GovernanceIntegrityAuditBookmarkService(
        InMemoryGovernanceIntegrityAuditBookmarkRepository(),
        history_repository,
        clock=clock,
    )


# --- Model -------------------------------------------------------------


def test_bookmark_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityAuditBookmark(
            name="  ", audit_id="A", created_at=BASE_TIME
        )


def test_bookmark_rejects_empty_audit_id() -> None:
    with pytest.raises(ValueError, match="audit_id must not be empty"):
        GovernanceIntegrityAuditBookmark(
            name="baseline", audit_id="  ", created_at=BASE_TIME
        )


def test_bookmark_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditBookmark(
            name="baseline",
            audit_id="A",
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_bookmark_to_dict() -> None:
    bookmark = GovernanceIntegrityAuditBookmark(
        name="baseline", audit_id="A", created_at=BASE_TIME
    )

    assert bookmark.to_dict() == {
        "name": "baseline",
        "audit_id": "A",
        "created_at": BASE_TIME.isoformat(),
    }


# --- InMemory repository -------------------------------------------------


def test_in_memory_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityAuditBookmarkRepository()

    bookmark = GovernanceIntegrityAuditBookmark(
        name="baseline", audit_id="A", created_at=BASE_TIME
    )

    repository.save(bookmark)

    assert repository.get("baseline") == bookmark
    assert repository.exists("baseline")
    assert repository.get("missing") is None


def test_in_memory_repository_rejects_duplicate_save() -> None:
    repository = InMemoryGovernanceIntegrityAuditBookmarkRepository()

    repository.save(
        GovernanceIntegrityAuditBookmark(
            name="baseline", audit_id="A", created_at=BASE_TIME
        )
    )

    with pytest.raises(
        GovernanceIntegrityAuditBookmarkAlreadyExistsError
    ):
        repository.save(
            GovernanceIntegrityAuditBookmark(
                name="baseline", audit_id="B", created_at=BASE_TIME
            )
        )


def test_in_memory_repository_delete() -> None:
    repository = InMemoryGovernanceIntegrityAuditBookmarkRepository()

    repository.save(
        GovernanceIntegrityAuditBookmark(
            name="baseline", audit_id="A", created_at=BASE_TIME
        )
    )

    repository.delete("baseline")

    assert not repository.exists("baseline")


def test_in_memory_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityAuditBookmarkRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_in_memory_repository_list_sorted_by_name() -> None:
    repository = InMemoryGovernanceIntegrityAuditBookmarkRepository()

    repository.save(
        GovernanceIntegrityAuditBookmark(
            name="release-v1", audit_id="A", created_at=BASE_TIME
        )
    )
    repository.save(
        GovernanceIntegrityAuditBookmark(
            name="baseline", audit_id="B", created_at=BASE_TIME
        )
    )

    names = [bookmark.name for bookmark in repository.list()]

    assert names == ["baseline", "release-v1"]


# --- Service ---------------------------------------------------------------


def test_service_creates_bookmark() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    bookmark = service.create("baseline", audit.audit_id)

    assert bookmark.name == "baseline"
    assert bookmark.audit_id == "A"


def test_service_create_rejects_duplicate_name() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))
    history_repository.save(make_record(audit_id="B", offset_minutes=10))

    service = make_service(history_repository)

    service.create("baseline", "A")

    with pytest.raises(ValueError):
        service.create("baseline", "B")


def test_service_create_rejects_missing_audit() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    with pytest.raises(LookupError):
        service.create("x", "missing")


def test_service_bookmark_latest() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A", offset_minutes=0))
    history_repository.save(make_record(audit_id="B", offset_minutes=10))
    history_repository.save(make_record(audit_id="C", offset_minutes=20))

    service = make_service(history_repository)

    bookmark = service.bookmark_latest("baseline")

    assert bookmark.audit_id == "C"


def test_service_bookmark_latest_raises_for_empty_history() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    with pytest.raises(LookupError):
        service.bookmark_latest("baseline")


def test_service_get_and_list() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))
    history_repository.save(make_record(audit_id="B", offset_minutes=10))

    service = make_service(history_repository)

    service.create("baseline", "A")
    service.create("release", "B")

    assert service.get("baseline").audit_id == "A"
    assert service.get("missing") is None
    assert len(service.list()) == 2


def test_service_delete() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))

    service = make_service(history_repository)

    service.create("baseline", "A")

    service.delete("baseline")

    assert service.get("baseline") is None


def test_service_delete_missing_raises_key_error() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    with pytest.raises(KeyError):
        service.delete("missing")


def test_service_uses_injected_clock() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))

    fixed_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

    service = make_service(history_repository, clock=lambda: fixed_time)

    bookmark = service.create("baseline", "A")

    assert bookmark.created_at == fixed_time


# --- SQLite repository -------------------------------------------------


def test_sqlite_repository_save_and_get(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "bookmarks.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditBookmarkRepository(
        database
    )

    bookmark = GovernanceIntegrityAuditBookmark(
        name="baseline", audit_id="A", created_at=BASE_TIME
    )

    repository.save(bookmark)

    retrieved = repository.get("baseline")

    assert retrieved is not None
    assert retrieved.name == "baseline"
    assert retrieved.audit_id == "A"


def test_sqlite_repository_rejects_duplicate_save(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "bookmarks-dup.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditBookmarkRepository(
        database
    )

    repository.save(
        GovernanceIntegrityAuditBookmark(
            name="baseline", audit_id="A", created_at=BASE_TIME
        )
    )

    with pytest.raises(
        GovernanceIntegrityAuditBookmarkAlreadyExistsError
    ):
        repository.save(
            GovernanceIntegrityAuditBookmark(
                name="baseline", audit_id="B", created_at=BASE_TIME
            )
        )


def test_sqlite_repository_delete_missing_raises_key_error(
    tmp_path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "bookmarks-missing.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditBookmarkRepository(
        database
    )

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_sqlite_repository_persists_across_runtime_reload(
    tmp_path,
) -> None:
    database_path = tmp_path / "bookmarks-reload.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    runtime.build_integrity_audit_bookmark_service().create(
        "baseline", "A"
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    bookmark = reloaded_runtime.build_integrity_audit_bookmark_service().get(
        "baseline"
    )

    assert bookmark is not None
    assert bookmark.audit_id == "A"


def test_runtime_builds_working_bookmark_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "bookmark-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_audit_bookmark_service()

    bookmark = service.create("baseline", "A")

    assert bookmark.name == "baseline"
