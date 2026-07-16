from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_labels import (
    GovernanceIntegrityAuditLabel,
    GovernanceIntegrityAuditLabelAlreadyExistsError,
    GovernanceIntegrityAuditLabelService,
    InMemoryGovernanceIntegrityAuditLabelRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_audit_labels import (
    SQLiteGovernanceIntegrityAuditLabelRepository,
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
) -> GovernanceIntegrityAuditLabelService:
    return GovernanceIntegrityAuditLabelService(
        InMemoryGovernanceIntegrityAuditLabelRepository(),
        history_repository,
        clock=clock,
    )


# --- Model -------------------------------------------------------------


def test_label_rejects_empty_audit_id() -> None:
    with pytest.raises(ValueError, match="audit_id must not be empty"):
        GovernanceIntegrityAuditLabel(
            audit_id="  ", label="release", created_at=BASE_TIME
        )


def test_label_rejects_empty_label() -> None:
    with pytest.raises(ValueError, match="label must not be empty"):
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="  ", created_at=BASE_TIME
        )


def test_label_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditLabel(
            audit_id="A",
            label="release",
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_label_to_dict() -> None:
    label = GovernanceIntegrityAuditLabel(
        audit_id="A", label="release", created_at=BASE_TIME
    )

    assert label.to_dict() == {
        "audit_id": "A",
        "label": "release",
        "created_at": BASE_TIME.isoformat(),
    }


# --- InMemory repository -------------------------------------------------


def test_in_memory_repository_add_and_query() -> None:
    repository = InMemoryGovernanceIntegrityAuditLabelRepository()

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )

    assert repository.labels("A") == ("release",)
    assert repository.audits("release") == ("A",)


def test_in_memory_repository_rejects_duplicate_add() -> None:
    repository = InMemoryGovernanceIntegrityAuditLabelRepository()

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )

    with pytest.raises(
        GovernanceIntegrityAuditLabelAlreadyExistsError
    ):
        repository.add(
            GovernanceIntegrityAuditLabel(
                audit_id="A", label="release", created_at=BASE_TIME
            )
        )


def test_in_memory_repository_remove() -> None:
    repository = InMemoryGovernanceIntegrityAuditLabelRepository()

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )

    repository.remove("A", "release")

    assert repository.labels("A") == ()


def test_in_memory_repository_remove_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityAuditLabelRepository()

    with pytest.raises(KeyError):
        repository.remove("A", "missing")


def test_in_memory_repository_list_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityAuditLabelRepository()

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )
    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="B",
            label="baseline",
            created_at=BASE_TIME + timedelta(minutes=10),
        )
    )

    records = repository.list()

    assert [record.audit_id for record in records] == ["B", "A"]


# --- Service ---------------------------------------------------------------


def test_service_adds_label() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.add(audit.audit_id, "release")

    assert "release" in service.labels(audit.audit_id)


def test_service_add_rejects_duplicate() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))

    service = make_service(history_repository)

    service.add("A", "release")

    with pytest.raises(ValueError):
        service.add("A", "release")


def test_service_add_rejects_missing_audit() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_service(history_repository)

    with pytest.raises(LookupError):
        service.add("missing", "release")


def test_service_removes_label() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    service.add(audit.audit_id, "release")

    service.remove(audit.audit_id, "release")

    assert service.labels(audit.audit_id) == ()


def test_service_remove_missing_raises_key_error() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    audit = make_record(audit_id="A")

    history_repository.save(audit)

    service = make_service(history_repository)

    with pytest.raises(KeyError):
        service.remove(audit.audit_id, "missing")


def test_service_search_returns_all_matching_audits() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A", offset_minutes=0))
    history_repository.save(make_record(audit_id="B", offset_minutes=10))
    history_repository.save(make_record(audit_id="C", offset_minutes=20))

    service = make_service(history_repository)

    service.add("A", "release")
    service.add("B", "baseline")
    service.add("C", "release")

    audits = service.audits("release")

    assert set(audits) == {"A", "C"}
    assert len(audits) == 2


def test_service_uses_injected_clock() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))

    fixed_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

    service = make_service(history_repository, clock=lambda: fixed_time)

    record = service.add("A", "release")

    assert record.created_at == fixed_time


def test_service_list_returns_all_labels() -> None:
    history_repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    history_repository.save(make_record(audit_id="A"))
    history_repository.save(make_record(audit_id="B", offset_minutes=10))

    service = make_service(history_repository)

    service.add("A", "release")
    service.add("B", "baseline")

    assert len(service.list()) == 2


# --- SQLite repository -------------------------------------------------


def test_sqlite_repository_add_and_query(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "labels.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditLabelRepository(database)

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )

    assert repository.labels("A") == ("release",)
    assert repository.audits("release") == ("A",)


def test_sqlite_repository_rejects_duplicate_add(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "labels-dup.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditLabelRepository(database)

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )

    with pytest.raises(
        GovernanceIntegrityAuditLabelAlreadyExistsError
    ):
        repository.add(
            GovernanceIntegrityAuditLabel(
                audit_id="A", label="release", created_at=BASE_TIME
            )
        )


def test_sqlite_repository_remove_missing_raises_key_error(
    tmp_path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "labels-missing.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditLabelRepository(database)

    with pytest.raises(KeyError):
        repository.remove("A", "missing")


def test_sqlite_repository_allows_multiple_audits_per_label(
    tmp_path,
) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "labels-multi.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditLabelRepository(database)

    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="A", label="release", created_at=BASE_TIME
        )
    )
    repository.add(
        GovernanceIntegrityAuditLabel(
            audit_id="B",
            label="release",
            created_at=BASE_TIME + timedelta(minutes=10),
        )
    )

    assert set(repository.audits("release")) == {"A", "B"}


def test_sqlite_repository_persists_across_runtime_reload(
    tmp_path,
) -> None:
    database_path = tmp_path / "labels-reload.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    runtime.build_integrity_audit_label_service().add("A", "release")

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    labels = reloaded_runtime.build_integrity_audit_label_service().labels(
        "A"
    )

    assert labels == ("release",)


def test_runtime_builds_working_label_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "label-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_audit_label_service()

    record = service.add("A", "release")

    assert record.label == "release"
