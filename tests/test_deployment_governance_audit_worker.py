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
    GovernanceIntegrityAuditExecutionRecord,
    GovernanceIntegrityAuditWorker,
    GovernanceIntegrityExecutionResult,
    InMemoryGovernanceIntegrityAuditExecutionRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


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
        self.queue_repository = (
            InMemoryGovernanceIntegrityAuditExecutionQueueRepository()
        )
        self.execution_repository = (
            InMemoryGovernanceIntegrityAuditExecutionRepository()
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
            clock=clock,
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


def test_record_rejects_empty_job_id() -> None:
    with pytest.raises(ValueError, match="job_id must not be empty"):
        GovernanceIntegrityAuditExecutionRecord(
            job_id="  ",
            template_name="release",
            result=GovernanceIntegrityExecutionResult.FAILED,
            report=None,
            error="boom",
            started_at=BASE_TIME,
            finished_at=BASE_TIME,
        )


def test_record_rejects_naive_started_at() -> None:
    with pytest.raises(
        ValueError, match="started_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditExecutionRecord(
            job_id="job-1",
            template_name="release",
            result=GovernanceIntegrityExecutionResult.FAILED,
            report=None,
            error="boom",
            started_at=datetime(2026, 7, 15, 23, 0, 0),
            finished_at=BASE_TIME,
        )


def test_record_rejects_finished_before_started() -> None:
    with pytest.raises(
        ValueError,
        match="finished_at must not be earlier than started_at",
    ):
        GovernanceIntegrityAuditExecutionRecord(
            job_id="job-1",
            template_name="release",
            result=GovernanceIntegrityExecutionResult.FAILED,
            report=None,
            error="boom",
            started_at=BASE_TIME,
            finished_at=BASE_TIME - timedelta(minutes=1),
        )


def test_record_rejects_success_without_report() -> None:
    with pytest.raises(
        ValueError, match="report must be set when result is SUCCESS"
    ):
        GovernanceIntegrityAuditExecutionRecord(
            job_id="job-1",
            template_name="release",
            result=GovernanceIntegrityExecutionResult.SUCCESS,
            report=None,
            error=None,
            started_at=BASE_TIME,
            finished_at=BASE_TIME,
        )


def test_record_rejects_failed_without_error() -> None:
    with pytest.raises(
        ValueError, match="error must be set when result is FAILED"
    ):
        GovernanceIntegrityAuditExecutionRecord(
            job_id="job-1",
            template_name="release",
            result=GovernanceIntegrityExecutionResult.FAILED,
            report=None,
            error=None,
            started_at=BASE_TIME,
            finished_at=BASE_TIME,
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    record = GovernanceIntegrityAuditExecutionRecord(
        job_id="job-1",
        template_name="release",
        result=GovernanceIntegrityExecutionResult.FAILED,
        report=None,
        error="boom",
        started_at=BASE_TIME,
        finished_at=BASE_TIME,
    )

    repository.save(record)

    assert repository.get("job-1") == record


def test_repository_get_missing_returns_none() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    assert repository.get("missing") is None


def test_repository_list_returns_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    first = GovernanceIntegrityAuditExecutionRecord(
        job_id="job-1",
        template_name="release",
        result=GovernanceIntegrityExecutionResult.FAILED,
        report=None,
        error="boom",
        started_at=BASE_TIME,
        finished_at=BASE_TIME,
    )
    second = GovernanceIntegrityAuditExecutionRecord(
        job_id="job-2",
        template_name="release",
        result=GovernanceIntegrityExecutionResult.FAILED,
        report=None,
        error="boom",
        started_at=BASE_TIME,
        finished_at=BASE_TIME + timedelta(minutes=1),
    )

    repository.save(first)
    repository.save(second)

    records = repository.list()

    assert records[0].job_id == "job-2"
    assert records[1].job_id == "job-1"


def test_repository_clear_empties_store() -> None:
    repository = InMemoryGovernanceIntegrityAuditExecutionRepository()

    repository.save(
        GovernanceIntegrityAuditExecutionRecord(
            job_id="job-1",
            template_name="release",
            result=GovernanceIntegrityExecutionResult.FAILED,
            report=None,
            error="boom",
            started_at=BASE_TIME,
            finished_at=BASE_TIME,
        )
    )

    repository.clear()

    assert repository.list() == ()


# --- Worker: run_job -------------------------------------------------------


def test_run_job_succeeds_and_generates_report() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    record = harness.worker.run_job(job.job_id)

    assert record.result is GovernanceIntegrityExecutionResult.SUCCESS
    assert record.report is not None
    assert record.error is None
    assert harness.queue_service.list() == ()


def test_run_job_stores_execution_record() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    record = harness.worker.run_job(job.job_id)

    assert harness.worker.get(job.job_id) == record


def test_run_job_fails_for_missing_template() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    harness.template_service.delete("nightly")

    record = harness.worker.run_job(job.job_id)

    assert record.result is GovernanceIntegrityExecutionResult.FAILED
    assert record.error is not None
    assert record.report is None
    assert harness.queue_service.list() == ()


def test_run_job_raises_for_missing_job() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.worker.run_job("missing")


# --- Worker: run_all -------------------------------------------------------


def test_run_all_runs_every_queued_job() -> None:
    harness = Harness()

    harness.create_schedule("job1")
    harness.create_schedule("job2")
    harness.create_schedule("job3")

    harness.queue_service.enqueue_schedule("job1")
    harness.queue_service.enqueue_schedule("job2")
    harness.queue_service.enqueue_schedule("job3")

    records = harness.worker.run_all()

    assert len(records) == 3
    assert all(
        record.result is GovernanceIntegrityExecutionResult.SUCCESS
        for record in records
    )
    assert harness.queue_service.list() == ()


def test_run_all_returns_empty_when_queue_is_empty() -> None:
    harness = Harness()

    assert harness.worker.run_all() == ()


# --- Worker: history/get/clear_history --------------------------------------


def test_history_returns_stored_records() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    record = harness.worker.run_job(job.job_id)

    assert harness.worker.history() == (record,)


def test_get_returns_none_for_missing_record() -> None:
    harness = Harness()

    assert harness.worker.get("missing") is None


def test_clear_history_empties_repository() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    harness.worker.run_job(job.job_id)

    harness.worker.clear_history()

    assert harness.worker.history() == ()


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_worker(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "worker-runtime.db"
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

    worker = runtime.build_integrity_audit_worker()

    record = worker.run_job(job.job_id)

    assert record.result is GovernanceIntegrityExecutionResult.SUCCESS
