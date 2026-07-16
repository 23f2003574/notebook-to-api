from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_bookmarks import (
    InMemoryGovernanceIntegrityAuditBookmarkRepository,
)
from backend.observability.deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollectionService,
    InMemoryGovernanceIntegrityAuditCollectionRepository,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_labels import (
    InMemoryGovernanceIntegrityAuditLabelRepository,
)
from backend.observability.deployment_governance_audit_report_schedule import (
    GovernanceIntegrityAuditReportSchedule,
    GovernanceIntegrityAuditReportScheduleAlreadyExistsError,
    GovernanceIntegrityAuditReportScheduleService,
    GovernanceIntegrityReportScheduleFrequency,
    InMemoryGovernanceIntegrityAuditReportScheduleRepository,
)
from backend.observability.deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportSource,
    GovernanceIntegrityAuditReportTemplateService,
    InMemoryGovernanceIntegrityAuditReportTemplateRepository,
)
from backend.observability.deployment_governance_audit_reports import (
    GovernanceIntegrityAuditReportService,
)
from backend.observability.deployment_governance_audit_saved_queries import (
    GovernanceIntegritySavedAuditQueryService,
    InMemoryGovernanceIntegritySavedAuditQueryRepository,
)
from backend.observability.deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchService,
)
from backend.observability.deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditStatisticsService,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_audit_report_schedule import (
    SQLiteGovernanceIntegrityAuditReportScheduleRepository,
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
        self.collection_repository = (
            InMemoryGovernanceIntegrityAuditCollectionRepository()
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
        self.template_repository = (
            InMemoryGovernanceIntegrityAuditReportTemplateRepository()
        )
        self.schedule_repository = (
            InMemoryGovernanceIntegrityAuditReportScheduleRepository()
        )

        self.collection_service = GovernanceIntegrityAuditCollectionService(
            self.collection_repository, self.history_repository
        )

        self.statistics_service = GovernanceIntegrityAuditStatisticsService(
            self.history_repository
        )

        self.report_service = GovernanceIntegrityAuditReportService(
            self.history_repository,
            self.collection_repository,
            self.statistics_service,
        )

        self.search_service = GovernanceIntegrityAuditSearchService(
            self.history_repository,
            self.label_repository,
            self.bookmark_repository,
        )

        self.saved_query_service = GovernanceIntegritySavedAuditQueryService(
            self.saved_query_repository, self.search_service
        )

        self.template_service = GovernanceIntegrityAuditReportTemplateService(
            self.template_repository,
            self.report_service,
            self.collection_service,
            self.saved_query_service,
        )

        self.service = GovernanceIntegrityAuditReportScheduleService(
            self.schedule_repository,
            self.template_service,
            clock=clock,
        )

    def create_template(self, name: str = "release") -> str:
        self.collection_service.create(f"{name}-collection")
        self.template_service.create(
            name, f"{name} Report",
            GovernanceIntegrityAuditReportSource.COLLECTION,
            f"{name}-collection", "json",
        )
        return name


# --- Model -------------------------------------------------------------


def test_schedule_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityAuditReportSchedule(
            name="  ",
            template_name="release",
            frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
            enabled=True,
            created_at=BASE_TIME,
        )


def test_schedule_rejects_empty_template_name() -> None:
    with pytest.raises(
        ValueError, match="template_name must not be empty"
    ):
        GovernanceIntegrityAuditReportSchedule(
            name="nightly",
            template_name="  ",
            frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
            enabled=True,
            created_at=BASE_TIME,
        )


def test_schedule_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditReportSchedule(
            name="nightly",
            template_name="release",
            frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
            enabled=True,
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Service: create ---------------------------------------------------


def test_service_creates_schedule_enabled_by_default() -> None:
    harness = Harness()

    harness.create_template()

    schedule = harness.service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    assert schedule.enabled


def test_service_create_rejects_duplicate_name() -> None:
    harness = Harness()

    harness.create_template()

    harness.service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    with pytest.raises(ValueError):
        harness.service.create(
            "nightly", "release",
            GovernanceIntegrityReportScheduleFrequency.DAILY,
        )


def test_service_create_rejects_missing_template() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.create(
            "nightly", "missing",
            GovernanceIntegrityReportScheduleFrequency.DAILY,
        )


# --- Service: enable/disable ------------------------------------------


def test_service_enable_disable() -> None:
    harness = Harness()

    harness.create_template()

    harness.service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    harness.service.disable("nightly")

    assert not harness.service.get("nightly").enabled

    harness.service.enable("nightly")

    assert harness.service.get("nightly").enabled


def test_service_enable_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.enable("missing")


def test_service_disable_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.disable("missing")


# --- Service: due_schedules ---------------------------------------------


def test_due_schedules_returns_only_enabled() -> None:
    harness = Harness()

    harness.create_template("release")
    harness.create_template("weekly")

    harness.service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )
    harness.service.create(
        "weekly", "weekly",
        GovernanceIntegrityReportScheduleFrequency.WEEKLY,
    )

    harness.service.disable("weekly")

    due = harness.service.due_schedules()

    assert len(due) == 1
    assert due[0].name == "nightly"


# --- Service: delete/list/get ----------------------------------------------


def test_service_deletes_schedule() -> None:
    harness = Harness()

    harness.create_template()

    harness.service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    harness.service.delete("nightly")

    assert harness.schedule_repository.list() == ()


def test_service_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_service_uses_injected_clock() -> None:
    harness = Harness(
        clock=lambda: datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    )

    harness.create_template()

    schedule = harness.service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    assert schedule.created_at == datetime(
        2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc
    )


# --- InMemory repository -------------------------------------------------


def test_in_memory_repository_rejects_duplicate_save() -> None:
    repository = InMemoryGovernanceIntegrityAuditReportScheduleRepository()

    schedule = GovernanceIntegrityAuditReportSchedule(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(schedule)

    with pytest.raises(
        GovernanceIntegrityAuditReportScheduleAlreadyExistsError
    ):
        repository.save(schedule)


def test_in_memory_repository_update_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityAuditReportScheduleRepository()

    schedule = GovernanceIntegrityAuditReportSchedule(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        enabled=True,
        created_at=BASE_TIME,
    )

    with pytest.raises(KeyError):
        repository.update(schedule)


# --- SQLite repository -------------------------------------------------


def test_sqlite_repository_save_and_update(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "schedules.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditReportScheduleRepository(
        database
    )

    schedule = GovernanceIntegrityAuditReportSchedule(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(schedule)

    import dataclasses
    disabled_schedule = dataclasses.replace(schedule, enabled=False)

    repository.update(disabled_schedule)

    retrieved = repository.get("nightly")

    assert retrieved is not None
    assert retrieved.enabled is False


def test_sqlite_repository_rejects_duplicate_save(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "schedules-dup.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditReportScheduleRepository(
        database
    )

    schedule = GovernanceIntegrityAuditReportSchedule(
        name="nightly",
        template_name="release",
        frequency=GovernanceIntegrityReportScheduleFrequency.DAILY,
        enabled=True,
        created_at=BASE_TIME,
    )

    repository.save(schedule)

    with pytest.raises(
        GovernanceIntegrityAuditReportScheduleAlreadyExistsError
    ):
        repository.save(schedule)


def test_sqlite_repository_persists_across_runtime_reload(
    tmp_path,
) -> None:
    database_path = tmp_path / "schedules-reload.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create("release-v1")

    template_service = runtime.build_integrity_audit_report_template_service()
    template_service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    schedule_service = runtime.build_integrity_audit_report_schedule_service()
    schedule_service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    reloaded_schedule_service = (
        reloaded_runtime.build_integrity_audit_report_schedule_service()
    )

    schedule = reloaded_schedule_service.get("nightly")

    assert schedule is not None
    assert schedule.template_name == "release"


def test_runtime_builds_working_schedule_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "schedule-runtime.db"
        )
    )

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create("release-v1")

    template_service = runtime.build_integrity_audit_report_template_service()
    template_service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    service = runtime.build_integrity_audit_report_schedule_service()

    schedule = service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    assert schedule.name == "nightly"
