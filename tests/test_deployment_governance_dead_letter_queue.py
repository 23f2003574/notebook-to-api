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
    GovernanceIntegrityAuditWorker,
    InMemoryGovernanceIntegrityAuditExecutionRepository,
)
from backend.observability.deployment_governance_dead_letter_queue import (
    GovernanceIntegrityDeadLetterRecord,
    GovernanceIntegrityDeadLetterService,
    InMemoryGovernanceIntegrityDeadLetterRepository,
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
        self.dead_letter_repository = (
            InMemoryGovernanceIntegrityDeadLetterRepository()
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

        self.service = GovernanceIntegrityDeadLetterService(
            self.execution_repository,
            self.dead_letter_repository,
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


def test_record_rejects_empty_dead_letter_id() -> None:
    with pytest.raises(
        ValueError, match="dead_letter_id must not be empty"
    ):
        GovernanceIntegrityDeadLetterRecord(
            dead_letter_id="  ",
            job_id="job-1",
            template_name="release",
            error="boom",
            failed_at=BASE_TIME,
        )


def test_record_rejects_empty_error() -> None:
    with pytest.raises(ValueError, match="error must not be empty"):
        GovernanceIntegrityDeadLetterRecord(
            dead_letter_id="dlq-1",
            job_id="job-1",
            template_name="release",
            error="  ",
            failed_at=BASE_TIME,
        )


def test_record_rejects_naive_failed_at() -> None:
    with pytest.raises(
        ValueError, match="failed_at must be timezone-aware"
    ):
        GovernanceIntegrityDeadLetterRecord(
            dead_letter_id="dlq-1",
            job_id="job-1",
            template_name="release",
            error="boom",
            failed_at=datetime(2026, 7, 15, 23, 0, 0),
        )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityDeadLetterRepository()

    record = GovernanceIntegrityDeadLetterRecord(
        dead_letter_id="dlq-1",
        job_id="job-1",
        template_name="release",
        error="boom",
        failed_at=BASE_TIME,
    )

    repository.save(record)

    assert repository.get("job-1") == record


def test_repository_get_missing_returns_none() -> None:
    repository = InMemoryGovernanceIntegrityDeadLetterRepository()

    assert repository.get("missing") is None


def test_repository_list_returns_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityDeadLetterRepository()

    first = GovernanceIntegrityDeadLetterRecord(
        dead_letter_id="dlq-1",
        job_id="job-1",
        template_name="release",
        error="boom",
        failed_at=BASE_TIME,
    )
    second = GovernanceIntegrityDeadLetterRecord(
        dead_letter_id="dlq-2",
        job_id="job-2",
        template_name="release",
        error="boom",
        failed_at=BASE_TIME + timedelta(minutes=1),
    )

    repository.save(first)
    repository.save(second)

    records = repository.list()

    assert records[0].job_id == "job-2"
    assert records[1].job_id == "job-1"


def test_repository_delete_removes_record() -> None:
    repository = InMemoryGovernanceIntegrityDeadLetterRepository()

    repository.save(
        GovernanceIntegrityDeadLetterRecord(
            dead_letter_id="dlq-1",
            job_id="job-1",
            template_name="release",
            error="boom",
            failed_at=BASE_TIME,
        )
    )

    repository.delete("job-1")

    assert repository.get("job-1") is None


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityDeadLetterRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_clear_empties_store() -> None:
    repository = InMemoryGovernanceIntegrityDeadLetterRepository()

    repository.save(
        GovernanceIntegrityDeadLetterRecord(
            dead_letter_id="dlq-1",
            job_id="job-1",
            template_name="release",
            error="boom",
            failed_at=BASE_TIME,
        )
    )

    repository.clear()

    assert repository.list() == ()


# --- Service: archive ----------------------------------------------------


def test_archive_failure_stores_record() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    record = harness.service.archive(failed_job_id)

    assert record.job_id == failed_job_id


def test_archive_success_raises_value_error() -> None:
    harness = Harness()

    harness.create_schedule("nightly")

    job = harness.queue_service.enqueue_schedule("nightly")

    record = harness.worker.run_job(job.job_id)

    assert record.result.value == "success"

    with pytest.raises(ValueError):
        harness.service.archive(job.job_id)


def test_duplicate_archive_raises_value_error() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    harness.service.archive(failed_job_id)

    with pytest.raises(ValueError):
        harness.service.archive(failed_job_id)


def test_archive_missing_execution_raises_lookup_error() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.archive("missing")


def test_archive_uses_injected_uuid_factory() -> None:
    ids = iter(["fixed-dlq-id"])

    harness = Harness(uuid_factory=lambda: next(ids))

    failed_job_id = harness.run_failed_job("nightly")

    record = harness.service.archive(failed_job_id)

    assert record.dead_letter_id == "fixed-dlq-id"


# --- Service: get/list/delete/clear -------------------------------------


def test_list_returns_archived_records() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    record = harness.service.archive(failed_job_id)

    assert harness.service.list() == (record,)


def test_get_returns_none_for_missing_record() -> None:
    harness = Harness()

    assert harness.service.get("missing") is None


def test_delete_removes_record() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    harness.service.archive(failed_job_id)

    harness.service.delete(failed_job_id)

    assert harness.service.get(failed_job_id) is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_clear_empties_repository() -> None:
    harness = Harness()

    failed_job_id = harness.run_failed_job("nightly")

    harness.service.archive(failed_job_id)

    harness.service.clear()

    assert harness.service.list() == ()


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_dead_letter_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "dlq-runtime.db"
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

    dead_letter_service = runtime.build_integrity_dead_letter_service()

    dead_letter_record = dead_letter_service.archive(job.job_id)

    assert dead_letter_record.job_id == job.job_id
