from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollection,
    GovernanceIntegrityAuditCollectionAlreadyExistsError,
    GovernanceIntegrityAuditCollectionEntry,
    GovernanceIntegrityAuditCollectionEntryAlreadyExistsError,
    GovernanceIntegrityAuditCollectionService,
    InMemoryGovernanceIntegrityAuditCollectionRepository,
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
from backend.observability.sqlite_deployment_governance_audit_collections import (
    SQLiteGovernanceIntegrityAuditCollectionRepository,
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
) -> GovernanceIntegrityAuditCollectionService:
    return GovernanceIntegrityAuditCollectionService(
        InMemoryGovernanceIntegrityAuditCollectionRepository(),
        history_repository,
        clock=clock,
    )


# --- Models --------------------------------------------------------------


def test_collection_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityAuditCollection(
            name="  ", description=None, created_at=BASE_TIME
        )


def test_collection_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditCollection(
            name="release-v1",
            description=None,
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_collection_entry_rejects_empty_collection() -> None:
    with pytest.raises(ValueError, match="collection must not be empty"):
        GovernanceIntegrityAuditCollectionEntry(
            collection="  ", audit_id="A", added_at=BASE_TIME
        )


def test_collection_entry_rejects_empty_audit_id() -> None:
    with pytest.raises(ValueError, match="audit_id must not be empty"):
        GovernanceIntegrityAuditCollectionEntry(
            collection="release-v1", audit_id="  ", added_at=BASE_TIME
        )


def test_collection_entry_rejects_naive_added_at() -> None:
    with pytest.raises(
        ValueError, match="added_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditCollectionEntry(
            collection="release-v1",
            audit_id="A",
            added_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Service: collection operations ---------------------------------------


def test_service_creates_collection() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    collection = service.create("release-v1")

    assert collection.name == "release-v1"


def test_service_create_rejects_duplicate_name() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    service.create("release-v1")

    with pytest.raises(ValueError):
        service.create("release-v1")


def test_service_create_with_description() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    collection = service.create(
        "release-v1", description="First stable release"
    )

    assert collection.description == "First stable release"


def test_service_lists_collections() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    service.create("release-v1")
    service.create("incident-42")

    assert len(service.list()) == 2


def test_service_gets_collection() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    service.create("release-v1")

    assert service.get("release-v1").name == "release-v1"
    assert service.get("missing") is None


# --- Service: membership operations ---------------------------------------


def test_service_adds_audit_to_collection() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.create("release-v1")

    service.add("release-v1", audit.audit_id)

    assert audit.audit_id in service.audits("release-v1")


def test_service_add_rejects_missing_collection() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))

    service = make_service(history_repository)

    with pytest.raises(LookupError):
        service.add("missing", "A")


def test_service_add_rejects_missing_audit() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    service.create("release-v1")

    with pytest.raises(LookupError):
        service.add("release-v1", "missing")


def test_service_add_rejects_duplicate_membership() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.create("release-v1")

    service.add("release-v1", audit.audit_id)

    with pytest.raises(ValueError):
        service.add("release-v1", audit.audit_id)


def test_service_removes_audit_from_collection() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.create("release-v1")
    service.add("release-v1", audit.audit_id)

    service.remove("release-v1", audit.audit_id)

    assert audit.audit_id not in service.audits("release-v1")


def test_service_remove_missing_raises_key_error() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    service.create("release-v1")

    with pytest.raises(KeyError):
        service.remove("release-v1", "missing")


def test_service_delete_removes_collection_and_entries() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.create("release-v1")
    service.add("release-v1", audit.audit_id)

    service.delete("release-v1")

    assert service.get("release-v1") is None

    with pytest.raises(LookupError):
        service.add("release-v1", audit.audit_id)


def test_service_delete_missing_raises_key_error() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    with pytest.raises(KeyError):
        service.delete("missing")


def test_service_collections_for_audit() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.create("release-v1")
    service.create("stable")

    service.add("release-v1", audit.audit_id)
    service.add("stable", audit.audit_id)

    assert set(service.collections(audit.audit_id)) == {
        "release-v1", "stable",
    }


def test_service_uses_injected_clock() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    fixed_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

    service = make_service(history_repository, clock=lambda: fixed_time)

    collection = service.create("release-v1")

    assert collection.created_at == fixed_time


# --- InMemory repository -------------------------------------------------


def test_in_memory_repository_rejects_duplicate_create() -> None:
    repository = InMemoryGovernanceIntegrityAuditCollectionRepository()

    collection = GovernanceIntegrityAuditCollection(
        name="release-v1", description=None, created_at=BASE_TIME
    )

    repository.create(collection)

    with pytest.raises(
        GovernanceIntegrityAuditCollectionAlreadyExistsError
    ):
        repository.create(collection)


def test_in_memory_repository_rejects_duplicate_membership() -> None:
    repository = InMemoryGovernanceIntegrityAuditCollectionRepository()

    repository.create(
        GovernanceIntegrityAuditCollection(
            name="release-v1", description=None, created_at=BASE_TIME
        )
    )

    repository.add_audit("release-v1", "A", added_at=BASE_TIME)

    with pytest.raises(
        GovernanceIntegrityAuditCollectionEntryAlreadyExistsError
    ):
        repository.add_audit("release-v1", "A", added_at=BASE_TIME)


def test_in_memory_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityAuditCollectionRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


# --- SQLite repository -------------------------------------------------


def test_sqlite_repository_create_and_membership(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "collections.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditCollectionRepository(
        database
    )

    repository.create(
        GovernanceIntegrityAuditCollection(
            name="release-v1",
            description="First release",
            created_at=BASE_TIME,
        )
    )

    repository.add_audit("release-v1", "A", added_at=BASE_TIME)
    repository.add_audit("release-v1", "B", added_at=BASE_TIME)

    assert set(repository.audits("release-v1")) == {"A", "B"}
    assert repository.collections("A") == ("release-v1",)


def test_sqlite_repository_delete_removes_entries(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "collections-delete.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditCollectionRepository(
        database
    )

    repository.create(
        GovernanceIntegrityAuditCollection(
            name="release-v1", description=None, created_at=BASE_TIME
        )
    )

    repository.add_audit("release-v1", "A", added_at=BASE_TIME)

    repository.delete("release-v1")

    assert repository.get("release-v1") is None
    assert repository.audits("release-v1") == ()


def test_sqlite_repository_rejects_duplicate_create(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "collections-dup.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditCollectionRepository(
        database
    )

    collection = GovernanceIntegrityAuditCollection(
        name="release-v1", description=None, created_at=BASE_TIME
    )

    repository.create(collection)

    with pytest.raises(
        GovernanceIntegrityAuditCollectionAlreadyExistsError
    ):
        repository.create(collection)


def test_sqlite_repository_persists_across_runtime_reload(
    tmp_path,
) -> None:
    database_path = tmp_path / "collections-reload.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_audit_collection_service()
    service.create("release-v1")
    service.add("release-v1", "A")

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    reloaded_service = (
        reloaded_runtime.build_integrity_audit_collection_service()
    )

    assert reloaded_service.get("release-v1") is not None
    assert reloaded_service.audits("release-v1") == ("A",)


def test_runtime_builds_working_collection_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "collection-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_audit_collection_service()

    service.create("release-v1")
    service.add("release-v1", "A")

    assert service.audits("release-v1") == ("A",)
