from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Protocol, runtime_checkable


class GovernanceIntegrityAuditOutcome(
    str,
    Enum,
):
    """
    Final outcome of one completed governance persistence integrity audit.
    """

    HEALTHY = "healthy"

    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class GovernanceIntegrityAuditRecord:
    """
    Storage-neutral historical record of one completed integrity audit.

    The record stores the aggregate audit result rather than every individual
    finding. Detailed findings remain part of the live audit report and can be
    persisted separately in a future extension if required.
    """

    audit_id: str

    backend: str

    started_at: datetime

    completed_at: datetime

    outcome: GovernanceIntegrityAuditOutcome

    total_records: int

    valid_records: int

    invalid_records: int

    integrity_mismatches: int

    missing_integrity_metadata: int

    invalid_integrity_metadata: int

    invalid_persisted_records: int

    def __post_init__(
        self,
    ) -> None:
        if not self.audit_id.strip():
            raise ValueError(
                "audit_id must not be empty"
            )

        if not self.backend.strip():
            raise ValueError(
                "backend must not be empty"
            )

        if (
            self.completed_at
            < self.started_at
        ):
            raise ValueError(
                "completed_at must not precede started_at"
            )

        counters = {
            "total_records": self.total_records,
            "valid_records": self.valid_records,
            "invalid_records": self.invalid_records,
            "integrity_mismatches": (
                self.integrity_mismatches
            ),
            "missing_integrity_metadata": (
                self.missing_integrity_metadata
            ),
            "invalid_integrity_metadata": (
                self.invalid_integrity_metadata
            ),
            "invalid_persisted_records": (
                self.invalid_persisted_records
            ),
        }

        for name, value in counters.items():
            if value < 0:
                raise ValueError(
                    f"{name} must not be negative"
                )

        if (
            self.valid_records
            + self.invalid_records
            != self.total_records
        ):
            raise ValueError(
                "valid_records + invalid_records "
                "must equal total_records"
            )

        classified_invalid_records = (
            self.integrity_mismatches
            + self.missing_integrity_metadata
            + self.invalid_integrity_metadata
            + self.invalid_persisted_records
        )

        if (
            classified_invalid_records
            != self.invalid_records
        ):
            raise ValueError(
                "integrity failure counters must sum "
                "to invalid_records"
            )

        expected_outcome = (
            GovernanceIntegrityAuditOutcome.HEALTHY
            if self.invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        )

        if (
            self.outcome
            is not expected_outcome
        ):
            raise ValueError(
                "audit outcome does not match "
                "the recorded integrity counters"
            )

    @property
    def healthy(
        self,
    ) -> bool:
        return (
            self.outcome
            is GovernanceIntegrityAuditOutcome.HEALTHY
        )

    @property
    def duration_seconds(
        self,
    ) -> float:
        return (
            self.completed_at
            - self.started_at
        ).total_seconds()


@dataclass(frozen=True)
class GovernanceIntegrityAuditHistoryQuery:
    """
    Query criteria for historical integrity audit records.
    """

    backend: str | None = None

    outcome: GovernanceIntegrityAuditOutcome | None = None

    started_at_or_after: datetime | None = None

    started_at_or_before: datetime | None = None

    limit: int | None = None

    def __post_init__(
        self,
    ) -> None:
        if (
            self.backend is not None
            and not self.backend.strip()
        ):
            raise ValueError(
                "backend must not be empty when provided"
            )

        if (
            self.limit is not None
            and self.limit <= 0
        ):
            raise ValueError(
                "limit must be greater than zero"
            )

        if (
            self.started_at_or_after is not None
            and self.started_at_or_before is not None
            and self.started_at_or_after
            > self.started_at_or_before
        ):
            raise ValueError(
                "started_at_or_after must not be later "
                "than started_at_or_before"
            )


class GovernanceIntegrityAuditHistoryError(
    RuntimeError
):
    """
    Base error for integrity audit history persistence failures.
    """


class GovernanceIntegrityAuditAlreadyExistsError(
    GovernanceIntegrityAuditHistoryError
):
    """
    Raised when an audit record with the same identifier already exists.
    """


@runtime_checkable
class GovernanceIntegrityAuditHistoryRepository(
    Protocol
):
    """
    Persistence contract for completed governance integrity audits.
    """

    def save(
        self,
        record: GovernanceIntegrityAuditRecord,
    ) -> GovernanceIntegrityAuditRecord:
        """
        Persist one completed integrity audit.
        """

    def get_by_audit_id(
        self,
        audit_id: str,
    ) -> GovernanceIntegrityAuditRecord | None:
        """
        Return one audit record by identifier.
        """

    def latest(
        self,
    ) -> GovernanceIntegrityAuditRecord | None:
        """
        Return the most recently started audit.
        """

    def list(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]:
        """
        Return audit records in reverse chronological order.
        """

    def query(
        self,
        query: GovernanceIntegrityAuditHistoryQuery,
    ) -> tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]:
        """
        Return audit records matching the supplied criteria.
        """

    def count(
        self,
    ) -> int:
        """
        Return the number of persisted audit records.
        """

    def count_by_outcome(
        self,
        outcome: GovernanceIntegrityAuditOutcome,
    ) -> int:
        """
        Return the number of persisted audit records with the given outcome.
        """

    def delete_by_ids(
        self,
        audit_ids: tuple[str, ...],
    ) -> int:
        """
        Delete audit records by identifier.

        Unknown IDs are ignored and duplicate IDs are not double-counted.
        Return the number of records actually deleted. The repository does
        not decide which IDs are prunable; that is retention policy, owned
        by GovernanceIntegrityAuditRetentionService.
        """


class InMemoryGovernanceIntegrityAuditHistoryRepository:
    """
    Thread-safe in-memory implementation of integrity audit history storage.

    This implementation acts as the reference behavior for future durable
    repository implementations.
    """

    def __init__(
        self,
    ) -> None:
        self._records: dict[
            str,
            GovernanceIntegrityAuditRecord,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        record: GovernanceIntegrityAuditRecord,
    ) -> GovernanceIntegrityAuditRecord:
        with self._lock:
            if (
                record.audit_id
                in self._records
            ):
                raise (
                    GovernanceIntegrityAuditAlreadyExistsError(
                        "governance integrity audit "
                        f"'{record.audit_id}' already exists"
                    )
                )

            self._records[
                record.audit_id
            ] = record

            return record

    def get_by_audit_id(
        self,
        audit_id: str,
    ) -> GovernanceIntegrityAuditRecord | None:
        normalized_audit_id = (
            audit_id.strip()
        )

        if not normalized_audit_id:
            raise ValueError(
                "audit_id must not be empty"
            )

        with self._lock:
            return self._records.get(
                normalized_audit_id
            )

    def latest(
        self,
    ) -> GovernanceIntegrityAuditRecord | None:
        with self._lock:
            if not self._records:
                return None

            return max(
                self._records.values(),
                key=self._sort_key,
            )

    def list(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]:
        if (
            limit is not None
            and limit <= 0
        ):
            raise ValueError(
                "limit must be greater than zero"
            )

        with self._lock:
            records = sorted(
                self._records.values(),
                key=self._sort_key,
                reverse=True,
            )

            if limit is not None:
                records = records[
                    :limit
                ]

            return tuple(
                records
            )

    def query(
        self,
        query: GovernanceIntegrityAuditHistoryQuery,
    ) -> tuple[
        GovernanceIntegrityAuditRecord,
        ...
    ]:
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if self._matches_query(
                    record,
                    query,
                )
            ]

            records.sort(
                key=self._sort_key,
                reverse=True,
            )

            if query.limit is not None:
                records = records[
                    :query.limit
                ]

            return tuple(
                records
            )

    def count(
        self,
    ) -> int:
        with self._lock:
            return len(
                self._records
            )

    def count_by_outcome(
        self,
        outcome: GovernanceIntegrityAuditOutcome,
    ) -> int:
        with self._lock:
            return sum(
                1
                for record in self._records.values()
                if record.outcome is outcome
            )

    def delete_by_ids(
        self,
        audit_ids: tuple[str, ...],
    ) -> int:
        unique_audit_ids = tuple(dict.fromkeys(audit_ids))

        if not unique_audit_ids:
            return 0

        deleted = 0

        with self._lock:
            for audit_id in unique_audit_ids:
                if audit_id in self._records:
                    del self._records[audit_id]
                    deleted += 1

        return deleted

    @staticmethod
    def _matches_query(
        record: GovernanceIntegrityAuditRecord,
        query: GovernanceIntegrityAuditHistoryQuery,
    ) -> bool:
        if (
            query.backend is not None
            and record.backend
            != query.backend
        ):
            return False

        if (
            query.outcome is not None
            and record.outcome
            is not query.outcome
        ):
            return False

        if (
            query.started_at_or_after
            is not None
            and record.started_at
            < query.started_at_or_after
        ):
            return False

        if (
            query.started_at_or_before
            is not None
            and record.started_at
            > query.started_at_or_before
        ):
            return False

        return True

    @staticmethod
    def _sort_key(
        record: GovernanceIntegrityAuditRecord,
    ) -> tuple[
        datetime,
        str,
    ]:
        return (
            record.started_at,
            record.audit_id,
        )
