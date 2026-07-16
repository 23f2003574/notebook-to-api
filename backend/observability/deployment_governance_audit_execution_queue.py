from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .deployment_governance_audit_report_schedule import (
        GovernanceIntegrityAuditReportScheduleService,
    )


class GovernanceIntegrityExecutionStatus(
    str,
    Enum,
):
    """
    Lifecycle status of a queued execution job.

    Only PENDING exists in this commit: the queue prepares runnable
    jobs but nothing consumes or executes them yet.
    """

    PENDING = "pending"


@dataclass(frozen=True)
class GovernanceIntegrityAuditExecutionJob:
    """
    One runnable unit of work converted from an enabled report schedule,
    ready for a future worker to pick up.
    """

    job_id: str

    schedule_name: str

    template_name: str

    status: GovernanceIntegrityExecutionStatus

    queued_at: datetime

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

        if self.queued_at.tzinfo is None:
            raise ValueError(
                "queued_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "schedule_name": self.schedule_name,
            "template_name": self.template_name,
            "status": self.status.value,
            "queued_at": self.queued_at.isoformat(),
        }


@runtime_checkable
class GovernanceIntegrityAuditExecutionQueueRepository(Protocol):
    """
    Persistence contract for queued governance audit execution jobs.
    """

    def enqueue(
        self,
        job: GovernanceIntegrityAuditExecutionJob,
    ) -> GovernanceIntegrityAuditExecutionJob:
        """
        Add one job to the queue.
        """

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionJob | None:
        """
        Return one job by identifier, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionJob,
        ...
    ]:
        """
        Return every queued job, newest to oldest.
        """

    def delete(
        self,
        job_id: str,
    ) -> None:
        """
        Remove one job by identifier. Raises KeyError if it does not
        exist.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every queued job.
        """


class InMemoryGovernanceIntegrityAuditExecutionQueueRepository:
    """
    Thread-safe in-memory implementation of the governance audit
    execution queue.

    SQLite persistence is intentionally deferred: this queue exists to
    prepare runnable jobs, not to durably track execution history, so
    an in-process queue is sufficient until a background worker exists
    to consume it.
    """

    def __init__(self) -> None:
        self._jobs: dict[
            str,
            GovernanceIntegrityAuditExecutionJob,
        ] = {}

        self._lock = RLock()

    def enqueue(
        self,
        job: GovernanceIntegrityAuditExecutionJob,
    ) -> GovernanceIntegrityAuditExecutionJob:
        with self._lock:
            self._jobs[job.job_id] = job

            return job

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionJob | None:
        normalized_job_id = self._normalize(job_id)

        with self._lock:
            return self._jobs.get(normalized_job_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionJob,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._jobs.values(),
                    key=lambda job: (job.queued_at, job.job_id),
                    reverse=True,
                )
            )

    def delete(
        self,
        job_id: str,
    ) -> None:
        normalized_job_id = self._normalize(job_id)

        with self._lock:
            if normalized_job_id not in self._jobs:
                raise KeyError(
                    f"execution job '{normalized_job_id}' was not found"
                )

            del self._jobs[normalized_job_id]

    def clear(
        self,
    ) -> None:
        with self._lock:
            self._jobs.clear()

    @staticmethod
    def _normalize(job_id: str) -> str:
        normalized_job_id = job_id.strip()

        if not normalized_job_id:
            raise ValueError(
                "job_id must not be empty"
            )

        return normalized_job_id


class GovernanceIntegrityAuditExecutionQueueService:
    """
    Converts enabled report schedules into runnable execution jobs.

    No background worker executes queued jobs yet; this service only
    prepares them.
    """

    def __init__(
        self,
        queue_repository: GovernanceIntegrityAuditExecutionQueueRepository,
        schedule_service: "GovernanceIntegrityAuditReportScheduleService",
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._queue_repository = queue_repository

        self._schedule_service = schedule_service

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._uuid_factory = uuid_factory or (
            lambda: str(uuid.uuid4())
        )

    def enqueue_schedule(
        self,
        schedule_name: str,
    ) -> GovernanceIntegrityAuditExecutionJob:
        """
        Convert one enabled schedule into a queued, pending job.

        Raises LookupError if the schedule does not exist, and
        ValueError if it is disabled.
        """

        schedule = self._schedule_service.get(schedule_name)

        if schedule is None:
            raise LookupError(
                f"report schedule '{schedule_name}' was not found"
            )

        if not schedule.enabled:
            raise ValueError(
                f"report schedule '{schedule_name}' is disabled"
            )

        job = GovernanceIntegrityAuditExecutionJob(
            job_id=self._uuid_factory(),
            schedule_name=schedule.name,
            template_name=schedule.template_name,
            status=GovernanceIntegrityExecutionStatus.PENDING,
            queued_at=self._clock(),
        )

        return self._queue_repository.enqueue(job)

    def enqueue_due(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionJob,
        ...
    ]:
        """
        Convert every currently due (enabled) schedule into a queued
        job.
        """

        return tuple(
            self.enqueue_schedule(schedule.name)
            for schedule in self._schedule_service.due_schedules()
        )

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityAuditExecutionJob,
        ...
    ]:
        return self._queue_repository.list()

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityAuditExecutionJob | None:
        return self._queue_repository.get(job_id)

    def delete(
        self,
        job_id: str,
    ) -> None:
        """
        Remove one job from the queue. Raises KeyError if it does not
        exist.
        """

        self._queue_repository.delete(job_id)

    def clear(
        self,
    ) -> None:
        """
        Remove every job from the queue.
        """

        self._queue_repository.clear()
