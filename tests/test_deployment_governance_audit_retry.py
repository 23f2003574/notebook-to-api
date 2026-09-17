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
    GovernanceIntegrityAuditExecutionQueueService,
    InMemoryGovernanceIntegrityAuditExecutionQueueRepository,
)
from backend.observability.deployment_governance_audit_history import (
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
from backend.observability.deployment_governance_audit_retry import (
    GovernanceIntegrityAuditRetryService,
    GovernanceIntegrityRetryRecord,
    InMemoryGovernanceIntegrityRetryRepository,
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
from backend.observability.deployment_governance_audit_worker import (
    GovernanceIntegrityAuditWorker,
    InMemoryGovernanceIntegrityAuditExecutionRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


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
        self.execution_repository = (
            InMemoryGovernanceIntegrityAuditExecutionRepository()
        )
        self.retry_repository = (
            InMemoryGovernanceIntegrityRetryRepository()
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

        self.queue_service = GovernanceIntegrityAuditExecutionQueueService(
            self.queue_repository, self.schedule_service
        )

        self.worker = GovernanceIntegrityAuditWorker(
            self.queue_service,
            self.template_service,
            self.execution_repository,
        )

        self.service = GovernanceIntegrityAuditRetryService(
            self.queue_service,
            self.execution_repository,
            self.retry_repository,
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

    def run_failed_job(self, schedule_name: str) -> str:
        """
        Queue and run a job that fails because its template was
        deleted before execution. Returns the failed job's id.
        """

        self.create_schedule(schedule_name)

        job = self.queue_service.enqueue_schedule(schedule_name)

        self.template_service.delete(schedule_name)

        record = self.worker.run_job(job.job_id)

        assert record.result.value == "failed"

        return job.job_id


# --- Model -------------------------------------------------------------


def test_record_rejects_empty_retry_id() -> None:
    with pytest.raises(ValueError, match="retry_id must not be empty"):
        GovernanceIntegrityRetryRecord(
            retry_id="  ",
            original_job_id="job-1",
            new_job_id="job-2",
            created_at=BASE_TIME,
        )


def test_record_rejects_empty_original_job_id() -> None:
    with pytest.raises(
        ValueError, match="original_job_id must not be empty"
    ):
        GovernanceIntegrityRetryRecord(
            retry_id="retry-1",
            original_job_id="  ",
            new_job_id="job-2",
            created_at=BASE_TIME,
        )


def test_record_rejects_naive_created_at() -> None:
    with pytest.raises(
        ValueError, match="created_at must be timezone-aware"
    ):
        GovernanceIntegrityRetryRecord(
            retry_id="retry-1",
            original_job_id="job-1",
            new_job_id="job-2",
            created_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityRetryRepository()

    record = GovernanceIntegrityRetryRecord(
        retry_id="retry-1",
        original_job_id="job-1",
        new_job_id="job-2",
        created_at=BASE_TIME,
    )

    repository.save(record)

    assert repository.get("job-1") == record


def test_repository_get_missing_returns_none() -> None:
    repository = InMemoryGovernanceIntegrityRetryRepository()

    assert repository.get("missing") is None


def test_repository_list_returns_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityRetryRepository()

    first = GovernanceIntegrityRetryRecord(
        retry_id="retry-1",
        original_job_id="job-1",
        new_job_id="job-2",
        created_at=BASE_TIME,
    )
    second = GovernanceIntegrityRetryRecord(
        retry_id="retry-2",
        original_job_id="job-3",
        new_job_id="job-4",
        created_at=BASE_TIME + timedelta(minutes=1),
    )

    repository.save(first)
    repository.save(second)

    records = repository.list()

    assert records[0].retry_id == "retry-2"
    assert records[1].retry_id == "retry-1"


def test_repository_clear_empties_store() -> None:
    repository = InMemoryGovernanceIntegrityRetryRepository()

    repository.save(
        GovernanceIntegrityRetryRecord(
            retry_id="retry-1",
            original_job_id="job-1",
            new_job_id="job-2",
            created_at=BASE_TIME,
        )
    )

    repository.clear()

    assert repository.list() == ()


# --- Service: retry ----------------------------------------------------


def test_retry_failed_job_queues_new_job() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    retry = harness.service.retry(failed_job_id)

    assert retry.original_job_id == failed_job_id
    assert retry.new_job_id != retry.original_job_id

    jobs = harness.queue_service.list()

    assert len(jobs) == 1
    assert jobs[0].job_id == retry.new_job_id


def test_retry_original_execution_is_unchanged() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    original_before = harness.execution_repository.get(failed_job_id)

    harness.service.retry(failed_job_id)

    original_after = harness.execution_repository.get(failed_job_id)

    assert original_before == original_after


def test_retry_success_raises_value_error() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    record = harness.worker.run_job(job.job_id)

    assert record.result.value == "success"

    with pytest.raises(ValueError):
        harness.service.retry(job.job_id)


def test_retry_missing_execution_raises_lookup_error() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.retry("missing")


def test_retry_uses_injected_uuid_factory() -> None:
    ids = iter(["fixed-retry-id"])

    harness = Harness(uuid_factory=lambda: next(ids))

    failed_job_id = harness.run_failed_job("nightly")

    retry = harness.service.retry(failed_job_id)

    assert retry.retry_id == "fixed-retry-id"


# --- Service: history/get/clear ---------------------------------------


def test_history_returns_retry_records() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    retry = harness.service.retry(failed_job_id)

    assert harness.service.history() == (retry,)


def test_get_returns_none_for_missing_record() -> None:
    harness = Harness()

    assert harness.service.get("missing") is None


def test_clear_empties_repository() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    harness.service.retry(failed_job_id)

    harness.service.clear()

    assert harness.service.history() == ()


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_retry_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "retry-runtime.db"
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

    template_service.delete("release")

    worker = runtime.build_integrity_audit_worker()
    record = worker.run_job(job.job_id)

    assert record.result.value == "failed"

    retry_service = runtime.build_integrity_audit_retry_service()

    retry = retry_service.retry(job.job_id)

    assert retry.original_job_id == job.job_id
    assert retry.new_job_id != job.job_id
