from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol, runtime_checkable

from .deployment_governance_audit_worker import (
    GovernanceIntegrityAuditExecutionRepository,
    GovernanceIntegrityExecutionResult,
)


@dataclass(frozen=True)
class GovernanceIntegrityDeadLetterRecord:
    """
    A permanently failed governance audit execution, preserved for
    manual investigation.
    """

    dead_letter_id: str

    job_id: str

    template_name: str

    error: str

    failed_at: datetime

    def __post_init__(self) -> None:
        if not self.dead_letter_id.strip():
            raise ValueError(
                "dead_letter_id must not be empty"
            )

        if not self.job_id.strip():
            raise ValueError(
                "job_id must not be empty"
            )

        if not self.template_name.strip():
            raise ValueError(
                "template_name must not be empty"
            )

        if not self.error.strip():
            raise ValueError(
                "error must not be empty"
            )

        if self.failed_at.tzinfo is None:
            raise ValueError(
                "failed_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "dead_letter_id": self.dead_letter_id,
            "job_id": self.job_id,
            "template_name": self.template_name,
            "error": self.error,
            "failed_at": self.failed_at.isoformat(),
        }


@runtime_checkable
class GovernanceIntegrityDeadLetterRepository(Protocol):
    """
    Persistence contract for governance audit dead letter records.
    """

    def save(
        self,
        record: GovernanceIntegrityDeadLetterRecord,
    ) -> GovernanceIntegrityDeadLetterRecord:
        """
        Persist one dead letter record, replacing any existing record
        for the same job id.
        """

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityDeadLetterRecord | None:
        """
        Return one dead letter record by job id, or None if it does
        not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeadLetterRecord,
        ...
    ]:
        """
        Return every dead letter record, newest to oldest.
        """

    def delete(
        self,
        job_id: str,
    ) -> None:
        """
        Remove one dead letter record by job id. Raises KeyError if it
        does not exist.
        """

    def clear(
        self,
    ) -> None:
        """
        Remove every dead letter record.
        """


class InMemoryGovernanceIntegrityDeadLetterRepository:
    """
    Thread-safe in-memory implementation of governance audit dead
    letter record storage.
    """

    def __init__(self) -> None:
        self._records: dict[
            str,
            GovernanceIntegrityDeadLetterRecord,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        record: GovernanceIntegrityDeadLetterRecord,
    ) -> GovernanceIntegrityDeadLetterRecord:
        with self._lock:
            self._records[record.job_id] = record

            return record

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityDeadLetterRecord | None:
        normalized_job_id = self._normalize(job_id)

        with self._lock:
            return self._records.get(normalized_job_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeadLetterRecord,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda record: (
                        record.failed_at,
                        record.job_id,
                    ),
                    reverse=True,
                )
            )

    def delete(
        self,
        job_id: str,
    ) -> None:
        normalized_job_id = self._normalize(job_id)

        with self._lock:
            if normalized_job_id not in self._records:
                raise KeyError(
                    f"dead letter record '{normalized_job_id}' "
                    "was not found"
                )

            del self._records[normalized_job_id]

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


class GovernanceIntegrityDeadLetterService:
    """
    Archives permanently failed governance audit executions for
    manual investigation.

    No automatic recovery: archived jobs stay archived until a human
    deletes the record or otherwise intervenes.
    """

    def __init__(
        self,
        execution_repository: GovernanceIntegrityAuditExecutionRepository,
        dead_letter_repository: GovernanceIntegrityDeadLetterRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._execution_repository = execution_repository

        self._dead_letter_repository = dead_letter_repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._uuid_factory = uuid_factory or (
            lambda: str(uuid.uuid4())
        )

    def archive(
        self,
        job_id: str,
    ) -> GovernanceIntegrityDeadLetterRecord:
        """
        Archive one failed execution into the dead letter queue.

        Raises LookupError if no execution record exists for job_id,
        and ValueError if that execution succeeded or was already
        archived.
        """

        execution = self._execution_repository.get(job_id)

        if execution is None:
            raise LookupError(
                f"execution record '{job_id}' was not found"
            )

        if execution.result is not GovernanceIntegrityExecutionResult.FAILED:
            raise ValueError(
                f"execution '{job_id}' did not fail and cannot be "
                "archived"
            )

        if self._dead_letter_repository.get(job_id) is not None:
            raise ValueError(
                f"execution '{job_id}' has already been archived"
            )

        assert execution.error is not None

        record = GovernanceIntegrityDeadLetterRecord(
            dead_letter_id=self._uuid_factory(),
            job_id=job_id,
            template_name=execution.template_name,
            error=execution.error,
            failed_at=self._clock(),
        )

        return self._dead_letter_repository.save(record)

    def get(
        self,
        job_id: str,
    ) -> GovernanceIntegrityDeadLetterRecord | None:
        return self._dead_letter_repository.get(job_id)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityDeadLetterRecord,
        ...
    ]:
        return self._dead_letter_repository.list()

    def delete(
        self,
        job_id: str,
    ) -> None:
        """
        Remove one dead letter record. Raises KeyError if it does not
        exist.
        """

        self._dead_letter_repository.delete(job_id)

    def clear(
        self,
    ) -> None:
        """
        Remove every dead letter record.
        """

        self._dead_letter_repository.clear()
