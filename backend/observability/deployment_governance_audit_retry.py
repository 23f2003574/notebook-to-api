from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_execution_queue import (
    GovernanceIntegrityAuditExecutionQueueService,
)
from .deployment_governance_audit_worker import (
    GovernanceIntegrityAuditExecutionRepository,
    GovernanceIntegrityExecutionResult,
)


@dataclass(frozen=True)
class GovernanceIntegrityRetryRecord:
    """
    A record of one retry: the failed execution it was created from,
    and the freshly queued job it produced.
    """

    retry_id: str

    original_job_id: str

    new_job_id: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.retry_id.strip():
            raise ValueError(
                "retry_id must not be empty"
            )

        if not self.original_job_id.strip():
            raise ValueError(
                "original_job_id must not be empty"
            )

        if not self.new_job_id.strip():
            raise ValueError(
                "new_job_id must not be empty"
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "retry_id": self.retry_id,
            "original_job_id": self.original_job_id,
            "new_job_id": self.new_job_id,
            "created_at": self.created_at.isoformat(),
        }


@runtime_checkable
class GovernanceIntegrityRetryRepository(Protocol):
    """
    Persistence contract for governance audit retry records.
    """

    def save(
        self,
        record: GovernanceIntegrityRetryRecord,
    ) -> GovernanceIntegrityRetryRecord:
        """
        Persist one retry record, replacing any existing record for
        the same original job id.
        """

    def get(
        self,
        original_job_id: str,
    ) -> GovernanceIntegrityRetryRecord | None:
        """
        Return the retry record for one original job id, or None if
        it was never retried.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityRetryRecord,
        ...
    ]:
        """
        Return every retry record, newest to oldest.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every retry record.
        """


class InMemoryGovernanceIntegrityRetryRepository:
    """
    Thread-safe in-memory implementation of governance audit retry
    record storage.
    """

    def __init__(self) -> None:
        self._records: dict[
            str,
            GovernanceIntegrityRetryRecord,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        record: GovernanceIntegrityRetryRecord,
    ) -> GovernanceIntegrityRetryRecord:
        with self._lock:
            self._records[record.original_job_id] = record

            return record

    def get(
        self,
        original_job_id: str,
    ) -> GovernanceIntegrityRetryRecord | None:
        normalized_job_id = self._normalize(original_job_id)

        with self._lock:
            return self._records.get(normalized_job_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityRetryRecord,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda record: (
                        record.created_at,
                        record.retry_id,
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
    def _normalize(original_job_id: str) -> str:
        normalized_job_id = original_job_id.strip()

        if not normalized_job_id:
            raise ValueError(
                "original_job_id must not be empty"
            )

        return normalized_job_id


class GovernanceIntegrityAuditRetryService:
    """
    Recovers failed governance audit execution jobs by re-queuing a
    fresh job for the same schedule, without touching the original
    failed execution record.
    """

    def __init__(
        self,
        queue_service: GovernanceIntegrityAuditExecutionQueueService,
        execution_repository: GovernanceIntegrityAuditExecutionRepository,
        retry_repository: GovernanceIntegrityRetryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._queue_service = queue_service

        self._execution_repository = execution_repository

        self._retry_repository = retry_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._uuid_factory = uuid_factory or (
            lambda: str(uuid.uuid4())
        )

    def retry(
        self,
        job_id: str,
    ) -> GovernanceIntegrityRetryRecord:
        """
        Re-queue a fresh job for a failed execution's schedule.

        Raises LookupError if no execution record exists for job_id,
        and ValueError if that execution did not fail. The original
        execution record is never modified.
        """

        execution = self._execution_repository.get(job_id)

        if execution is None:
            raise LookupError(
                f"execution record '{job_id}' was not found"
            )

        if execution.result is not GovernanceIntegrityExecutionResult.FAILED:
            raise ValueError(
                f"execution '{job_id}' did not fail and cannot be retried"
            )

        new_job = self._queue_service.enqueue_schedule(
            execution.schedule_name
        )

        record = GovernanceIntegrityRetryRecord(
            retry_id=self._uuid_factory(),
            original_job_id=job_id,
            new_job_id=new_job.job_id,
            created_at=self._clock(),
        )

        return self._retry_repository.save(record)

    def history(
        self,
    ) -> tuple[
        GovernanceIntegrityRetryRecord,
        ...
    ]:
        return self._retry_repository.list()

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityRetryRecord | None:
        return self._retry_repository.get(job_id)

    def clear(
        self,
    ) -> None:
        self._retry_repository.clear()
