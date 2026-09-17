from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_collections import (
    GovernanceIntegrityAuditCollectionService,
    InMemoryGovernanceIntegrityAuditCollectionRepository,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportSource,
    GovernanceIntegrityAuditReportTemplate,
    GovernanceIntegrityAuditReportTemplateAlreadyExistsError,
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
    GovernanceIntegrityAuditSearchQuery,
    GovernanceIntegrityAuditSearchService,
)
from backend.observability.deployment_governance_audit_statistics import (
    GovernanceIntegrityAuditStatisticsService,
)
from backend.observability.deployment_governance_audit_bookmarks import (
    InMemoryGovernanceIntegrityAuditBookmarkRepository,
)
from backend.observability.deployment_governance_audit_labels import (
    InMemoryGovernanceIntegrityAuditLabelRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_audit_report_templates import (
    SQLiteGovernanceIntegrityAuditReportTemplateRepository,
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

        self.service = GovernanceIntegrityAuditReportTemplateService(
            self.template_repository,
            self.report_service,
            self.collection_service,
            self.saved_query_service,
            clock=clock,
        )


# --- Model -------------------------------------------------------------


def test_template_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityAuditReportTemplate(
            name="  ",
            title="Release Report",
            source=GovernanceIntegrityAuditReportSource.COLLECTION,
            source_name="release-v1",
            output_format="json",
            created_at=BASE_TIME,
        )


def test_template_rejects_invalid_output_format() -> None:
    with pytest.raises(ValueError, match="output_format"):
        GovernanceIntegrityAuditReportTemplate(
            name="release",
            title="Release Report",
            source=GovernanceIntegrityAuditReportSource.COLLECTION,
            source_name="release-v1",
            output_format="pdf",
            created_at=BASE_TIME,
        )


def test_template_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditReportTemplate(
            name="release",
            title="Release Report",
            source=GovernanceIntegrityAuditReportSource.COLLECTION,
            source_name="release-v1",
            output_format="json",
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Service: create ---------------------------------------------------


def test_service_creates_template() -> None:
    harness = Harness()

    harness.collection_service.create("release-v1")

    template = harness.service.create(
        "release",
        "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1",
        "json",
    )

    assert template.name == "release"


def test_service_create_rejects_duplicate_name() -> None:
    harness = Harness()

    harness.collection_service.create("release-v1")

    harness.service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    with pytest.raises(ValueError):
        harness.service.create(
            "release", "Release Report",
            GovernanceIntegrityAuditReportSource.COLLECTION,
            "release-v1", "json",
        )


def test_service_create_rejects_missing_collection() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.create(
            "release", "Release Report",
            GovernanceIntegrityAuditReportSource.COLLECTION,
            "missing", "json",
        )


def test_service_create_rejects_missing_saved_query() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.create(
            "release", "Release Report",
            GovernanceIntegrityAuditReportSource.SAVED_QUERY,
            "missing", "json",
        )


# --- Service: generate ------------------------------------------------


def test_service_generates_report_from_collection() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10)
    )

    harness.collection_service.create("release-v1")
    harness.collection_service.add("release-v1", "A")
    harness.collection_service.add("release-v1", "B")

    harness.service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    report = harness.service.generate("release")

    assert {record.audit_id for record in report.audits} == {"A", "B"}
    assert report.title == "Release Report"


def test_service_generates_report_from_saved_query() -> None:
    harness = Harness()

    harness.history_repository.save(
        make_record(audit_id="A", offset_minutes=0, healthy=True)
    )
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10, healthy=False)
    )

    harness.saved_query_service.save(
        "healthy", GovernanceIntegrityAuditSearchQuery(healthy=True)
    )

    harness.service.create(
        "healthy-report", "Healthy Report",
        GovernanceIntegrityAuditReportSource.SAVED_QUERY,
        "healthy", "markdown",
    )

    report = harness.service.generate("healthy-report")

    assert {record.audit_id for record in report.audits} == {"A"}


def test_service_generate_raises_for_missing_template() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.generate("missing")


# --- Service: list/get/delete ---------------------------------------------


def test_service_deletes_template() -> None:
    harness = Harness()

    harness.collection_service.create("release-v1")

    harness.service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    harness.service.delete("release")

    assert harness.template_repository.list() == ()


def test_service_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_service_lists_and_gets_templates() -> None:
    harness = Harness()

    harness.collection_service.create("release-v1")

    harness.service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    assert len(harness.service.list()) == 1
    assert harness.service.get("release").name == "release"
    assert harness.service.get("missing") is None


# --- InMemory repository -------------------------------------------------


def test_in_memory_repository_rejects_duplicate_save() -> None:
    repository = InMemoryGovernanceIntegrityAuditReportTemplateRepository()

    template = GovernanceIntegrityAuditReportTemplate(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        output_format="json",
        created_at=BASE_TIME,
    )

    repository.save(template)

    with pytest.raises(
        GovernanceIntegrityAuditReportTemplateAlreadyExistsError
    ):
        repository.save(template)


# --- SQLite repository -------------------------------------------------


def test_sqlite_repository_save_and_get(tmp_path) -> None:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "templates.db",
        )
    )

    repository = SQLiteGovernanceIntegrityAuditReportTemplateRepository(
        database
    )

    template = GovernanceIntegrityAuditReportTemplate(
        name="release",
        title="Release Report",
        source=GovernanceIntegrityAuditReportSource.COLLECTION,
        source_name="release-v1",
        output_format="json",
        created_at=BASE_TIME,
    )

    repository.save(template)

    retrieved = repository.get("release")

    assert retrieved is not None
    assert retrieved.source is GovernanceIntegrityAuditReportSource.COLLECTION
    assert retrieved.source_name == "release-v1"


def test_sqlite_repository_persists_across_runtime_reload(
    tmp_path,
) -> None:
    database_path = tmp_path / "templates-reload.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create("release-v1")
    collection_service.add("release-v1", "A")

    template_service = runtime.build_integrity_audit_report_template_service()
    template_service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    reloaded_template_service = (
        reloaded_runtime.build_integrity_audit_report_template_service()
    )

    template = reloaded_template_service.get("release")

    assert template is not None
    assert template.source_name == "release-v1"

    report = reloaded_template_service.generate("release")

    assert len(report.audits) == 1


def test_runtime_builds_working_template_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "template-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    collection_service = runtime.build_integrity_audit_collection_service()
    collection_service.create("release-v1")
    collection_service.add("release-v1", "A")

    service = runtime.build_integrity_audit_report_template_service()

    service.create(
        "release", "Release Report",
        GovernanceIntegrityAuditReportSource.COLLECTION,
        "release-v1", "json",
    )

    report = service.generate("release")

    assert len(report.audits) == 1
