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
from backend.observability.deployment_governance_audit_execution_queue import (
    GovernanceIntegrityAuditExecutionJob,
    GovernanceIntegrityAuditExecutionQueueService,
    GovernanceIntegrityExecutionStatus,
    InMemoryGovernanceIntegrityAuditExecutionQueueRepository,
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


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
) -> GovernanceIntegrityAuditRecord:
    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        total_records=10,
        valid_records=10,
        invalid_records=0,
        integrity_mismatches=0,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


class Harness:
    def __init__(self, *, clock=None, uuid_factory=None) -> None:
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
        self.queue_repository = (
            InMemoryGovernanceIntegrityAuditExecutionQueueRepository()
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

        self.schedule_service = GovernanceIntegrityAuditReportScheduleService(
            self.schedule_repository, self.template_service
        )

        self.service = GovernanceIntegrityAuditExecutionQueueService(
            self.queue_repository,
            self.schedule_service,
            clock=clock,
            uuid_factory=uuid_factory,
        )

    def create_schedule(
        self,
        name: str,
        frequency: GovernanceIntegrityReportScheduleFrequency = (
            GovernanceIntegrityReportScheduleFrequency.DAILY
        ),
    ) -> None:
        self.collection_service.create(f"{name}-collection")
        self.template_service.create(
            name, f"{name} Report",
            GovernanceIntegrityAuditReportSource.COLLECTION,
            f"{name}-collection", "json",
        )
        self.schedule_service.create(name, name, frequency)


# --- Model -------------------------------------------------------------


def test_job_rejects_empty_job_id() -> None:
    with pytest.raises(ValueError, match="job_id must not be empty"):
        GovernanceIntegrityAuditExecutionJob(
            job_id="  ",
            schedule_name="nightly",
            template_name="release",
            status=GovernanceIntegrityExecutionStatus.PENDING,
            queued_at=BASE_TIME,
        )


def test_job_rejects_empty_schedule_name() -> None:
    with pytest.raises(
        ValueError, match="schedule_name must not be empty"
    ):
        GovernanceIntegrityAuditExecutionJob(
            job_id="job-1",
            schedule_name="  ",
            template_name="release",
            status=GovernanceIntegrityExecutionStatus.PENDING,
            queued_at=BASE_TIME,
        )


def test_job_rejects_naive_queued_at() -> None:
    with pytest.raises(
        ValueError, match="queued_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditExecutionJob(
            job_id="job-1",
            schedule_name="nightly",
            template_name="release",
            status=GovernanceIntegrityExecutionStatus.PENDING,
            queued_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Service: enqueue_schedule ------------------------------------------


def test_enqueue_schedule_returns_pending_job() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.service.enqueue_schedule("nightly")

    assert job.status is GovernanceIntegrityExecutionStatus.PENDING
    assert job.schedule_name == "nightly"
    assert job.template_name == "nightly"


def test_enqueue_schedule_rejects_disabled_schedule() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    harness.schedule_service.disable("nightly")

    with pytest.raises(ValueError):
        harness.service.enqueue_schedule("nightly")


def test_enqueue_schedule_raises_for_missing_schedule() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.enqueue_schedule("missing")


# --- Service: enqueue_due ------------------------------------------------


def test_enqueue_due_only_queues_enabled_schedules() -> None:
    harness = Harness()

    harness.create_schedule("nightly")
    harness.create_schedule("weekly")

    harness.schedule_service.disable("weekly")

    jobs = harness.service.enqueue_due()

    assert len(jobs) == 1
    assert jobs[0].schedule_name == "nightly"


def test_enqueue_due_returns_empty_when_none_enabled() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    harness.schedule_service.disable("nightly")

    assert harness.service.enqueue_due() == ()


# --- Service: list/get/delete/clear ----------------------------------------


def test_delete_removes_job() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.service.enqueue_schedule("nightly")

    harness.service.delete(job.job_id)

    assert harness.service.get(job.job_id) is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_clear_empties_queue() -> None:
    harness = Harness()

    harness.create_schedule("nightly")
    harness.create_schedule("weekly")

    harness.service.enqueue_schedule("nightly")
    harness.service.enqueue_schedule("weekly")

    harness.service.clear()

    assert harness.service.list() == ()


def test_list_returns_newest_first() -> None:
    timestamps = iter(
        [
            BASE_TIME,
            BASE_TIME + timedelta(minutes=1),
        ]
    )

    harness = Harness(clock=lambda: next(timestamps))

    harness.create_schedule("nightly")
    harness.create_schedule("weekly")

    first_job = harness.service.enqueue_schedule("nightly")
    second_job = harness.service.enqueue_schedule("weekly")

    jobs = harness.service.list()

    assert jobs[0].job_id == second_job.job_id
    assert jobs[1].job_id == first_job.job_id


def test_uses_injected_uuid_factory() -> None:
    ids = iter(["fixed-job-id"])

    harness = Harness(uuid_factory=lambda: next(ids))

    harness.create_schedule("nightly")

    job = harness.service.enqueue_schedule("nightly")

    assert job.job_id == "fixed-job-id"


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_queue_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "queue-runtime.db"
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

    schedule_service = runtime.build_integrity_audit_report_schedule_service()
    schedule_service.create(
        "nightly", "release",
        GovernanceIntegrityReportScheduleFrequency.DAILY,
    )

    queue_service = runtime.build_integrity_audit_execution_queue_service()

    job = queue_service.enqueue_schedule("nightly")

    assert job.status is GovernanceIntegrityExecutionStatus.PENDING
