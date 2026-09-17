from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_execution_queue import (
    GovernanceIntegrityAuditExecutionQueueService,
)
from .deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportTemplateService,
)
from .deployment_governance_audit_reports import (
    GovernanceIntegrityAuditReport,
)


class GovernanceIntegrityExecutionResult(
    str,
    Enum,
):
    """
    Outcome of one governance audit execution job run.
    """

    SUCCESS = "success"

    FAILED = "failed"


@dataclass(frozen=True)
class GovernanceIntegrityAuditExecutionRecord:
    """
    The durable outcome of running one queued governance audit
    execution job: either the generated report, or the reason it
    could not be generated.
    """

    job_id: str

    schedule_name: str

    template_name: str

    result: GovernanceIntegrityExecutionResult

    report: GovernanceIntegrityAuditReport | None

    error: str | None

    started_at: datetime

    finished_at: datetime

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError(
                "job_id must not be empty"
            )

        if not self.schedule_name.strip():
            raise ValueError(
                "schedule_name must not be empty"
            )

        if not self.template_name.strip():
            raise ValueError(
                "template_name must not be empty"
            )

        if self.started_at.tzinfo is None:
            raise ValueError(
                "started_at must be timezone-aware"
            )

        if self.finished_at.tzinfo is None:
            raise ValueError(
                "finished_at must be timezone-aware"
            )

        if self.finished_at < self.started_at:
            raise ValueError(
                "finished_at must not be earlier than started_at"
            )

        if self.result is GovernanceIntegrityExecutionResult.SUCCESS:
            if self.report is None:
                raise ValueError(
                    "report must be set when result is SUCCESS"
                )

            if self.error is not None:
                raise ValueError(
                    "error must not be set when result is SUCCESS"
                )

        else:
            if self.report is not None:
                raise ValueError(
                    "report must not be set when result is FAILED"
                )

            if self.error is None:
                raise ValueError(
                    "error must be set when result is FAILED"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "schedule_name": self.schedule_name,
            "template_name": self.template_name,
            "result": self.result.value,
            "report": (
                None
                if self.report is None
                else self.report.to_dict()
            ),
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
        }


@runtime_checkable
class GovernanceIntegrityAuditExecutionRepository(Protocol):
    """
    Persistence contract for governance audit execution records.
    """

    def save(
        self,
        record: GovernanceIntegrityAuditExecutionRecord,
    ) -> GovernanceIntegrityAuditExecutionRecord:
        """
        Persist one execution record, replacing any existing record
        for the same job id.
        """

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionRecord | None:
        """
        Return one execution record by job id, or None if it does not
        exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionRecord,
        ...
    ]:
        """
        Return every execution record, newest to oldest.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every execution record.
        """


class InMemoryGovernanceIntegrityAuditExecutionRepository:
    """
    Thread-safe in-memory implementation of governance audit
    execution record storage.
    """

    def __init__(self) -> None:
        self._records: dict[
            str,
            GovernanceIntegrityAuditExecutionRecord,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        record: GovernanceIntegrityAuditExecutionRecord,
    ) -> GovernanceIntegrityAuditExecutionRecord:
        with self._lock:
            self._records[record.job_id] = record

            return record

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionRecord | None:
        normalized_job_id = self._normalize(job_id)

        with self._lock:
            return self._records.get(normalized_job_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionRecord,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda record: (
                        record.finished_at,
                        record.job_id,
                    ),
                    reverse=True,
                )
            )

    def clear(
        self,
    ) -> None:
        with self._lock:
            self._records.clear()

    @staticmethod
    def _normalize(job_id: str) -> str:
        normalized_job_id = job_id.strip()

        if not normalized_job_id:
            raise ValueError(
                "job_id must not be empty"
            )

        return normalized_job_id


class GovernanceIntegrityAuditWorker:
    """
    Synchronously processes queued governance audit execution jobs
    into generated reports.

    Single-threaded only: run_job and run_all execute jobs one at a
    time, in-process, with no concurrency of their own.
    """

    def __init__(
        self,
        queue_service: GovernanceIntegrityAuditExecutionQueueService,
        template_service: GovernanceIntegrityAuditReportTemplateService,
        execution_repository: GovernanceIntegrityAuditExecutionRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._queue_service = queue_service

        self._template_service = template_service

        self._execution_repository = execution_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def run_job(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionRecord:
        """
        Load one queued job, generate its report, store the execution
        outcome, and remove the job from the queue.

        Raises KeyError if the job does not exist. Template lookup
        failures and report generation failures are captured as a
        FAILED execution record rather than raised.
        """

        job = self._queue_service.get(job_id)

        if job is None:
            raise KeyError(
                f"execution job '{job_id}' was not found"
            )

        started_at = self._clock()

        try:
            report = self._template_service.generate(
                job.template_name
            )

        except Exception as exc:
            record = GovernanceIntegrityAuditExecutionRecord(
                job_id=job.job_id,
                schedule_name=job.schedule_name,
                template_name=job.template_name,
                result=GovernanceIntegrityExecutionResult.FAILED,
                report=None,
                error=str(exc),
                started_at=started_at,
                finished_at=self._clock(),
            )

        else:
            record = GovernanceIntegrityAuditExecutionRecord(
                job_id=job.job_id,
                schedule_name=job.schedule_name,
                template_name=job.template_name,
                result=GovernanceIntegrityExecutionResult.SUCCESS,
                report=report,
                error=None,
                started_at=started_at,
                finished_at=self._clock(),
            )

        self._execution_repository.save(record)

        self._queue_service.delete(job.job_id)

        return record

    def run_all(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionRecord,
        ...
    ]:
        """
        Run every currently queued job, sequentially, oldest first.
        """

        jobs = sorted(
            self._queue_service.list(),
            key=lambda job: (job.queued_at, job.job_id),
        )

        return tuple(
            self.run_job(job.job_id)
            for job in jobs
        )

    def history(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionRecord,
        ...
    ]:
        return self._execution_repository.list()

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionRecord | None:
        return self._execution_repository.get(job_id)

    def clear_history(
        self,
    ) -> None:
        self._execution_repository.clear()
