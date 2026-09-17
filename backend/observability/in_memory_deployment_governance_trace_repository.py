from __future__ import annotations

from threading import RLock
from typing import Sequence

from .deployment_governance_trace_repository import (
    DeploymentGovernanceTraceRepository,
    GovernanceTraceQuery,
    GovernanceTraceRecord,
    GovernanceTraceRepositoryStatistics,
)


class GovernanceTraceAlreadyExistsError(ValueError):
    """
    Raised when a repository creation operation conflicts with an existing
    trace or deployment identifier.
    """


class GovernanceTraceNotFoundError(LookupError):
    """
    Raised when an operation requires an existing governance trace but the
    requested trace cannot be found.
    """


class InMemoryDeploymentGovernanceTraceRepository(
    DeploymentGovernanceTraceRepository
):
    """
    Thread-safe in-memory implementation of the deployment governance trace
    repository contract.

    This repository is intentionally non-durable. Its purpose is to provide:

    - deterministic local behavior,
    - fast unit-test storage,
    - a reference implementation for durable repositories,
    - dependency-injection compatibility.

    Repository semantics should remain consistent with future SQLite and
    PostgreSQL implementations.
    """

    def __init__(self) -> None:
        self._records_by_trace_id: dict[str, GovernanceTraceRecord] = {}
        self._trace_id_by_deployment_id: dict[str, str] = {}
        self._lock = RLock()

    def save(
        self,
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceRecord:
        """
        Persist a new governance trace.

        Both trace_id and deployment_id are treated as unique identifiers.
        """

        with self._lock:
            self._ensure_trace_id_available(record.trace_id)
            self._ensure_deployment_id_available(record.deployment_id)

            self._store(record)

            return record

    def update(
        self,
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceRecord:
        """
        Replace an existing governance trace.

        The trace must already exist. A deployment identifier may not be
        reassigned to a trace when that deployment is already owned by
        another trace.
        """

        with self._lock:
            existing = self._records_by_trace_id.get(record.trace_id)

            if existing is None:
                raise GovernanceTraceNotFoundError(
                    f"governance trace '{record.trace_id}' does not exist"
                )

            existing_trace_for_deployment = (
                self._trace_id_by_deployment_id.get(record.deployment_id)
            )

            if (
                existing_trace_for_deployment is not None
                and existing_trace_for_deployment != record.trace_id
            ):
                raise GovernanceTraceAlreadyExistsError(
                    "deployment "
                    f"'{record.deployment_id}' is already associated with "
                    f"governance trace '{existing_trace_for_deployment}'"
                )

            if existing.deployment_id != record.deployment_id:
                self._trace_id_by_deployment_id.pop(
                    existing.deployment_id,
                    None,
                )

            self._store(record)

            return record

    def upsert(
        self,
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceRecord:
        """
        Create or replace a governance trace.

        Upsert preserves deployment uniqueness even when the trace itself is
        new.
        """

        with self._lock:
            existing = self._records_by_trace_id.get(record.trace_id)

            deployment_owner = self._trace_id_by_deployment_id.get(
                record.deployment_id
            )

            if (
                deployment_owner is not None
                and deployment_owner != record.trace_id
            ):
                raise GovernanceTraceAlreadyExistsError(
                    "deployment "
                    f"'{record.deployment_id}' is already associated with "
                    f"governance trace '{deployment_owner}'"
                )

            if (
                existing is not None
                and existing.deployment_id != record.deployment_id
            ):
                self._trace_id_by_deployment_id.pop(
                    existing.deployment_id,
                    None,
                )

            self._store(record)

            return record

    def get_by_trace_id(
        self,
        trace_id: str,
    ) -> GovernanceTraceRecord | None:
        with self._lock:
            return self._records_by_trace_id.get(trace_id)

    def get_by_deployment_id(
        self,
        deployment_id: str,
    ) -> GovernanceTraceRecord | None:
        with self._lock:
            trace_id = self._trace_id_by_deployment_id.get(deployment_id)

            if trace_id is None:
                return None

            return self._records_by_trace_id.get(trace_id)

    def exists(
        self,
        trace_id: str,
    ) -> bool:
        with self._lock:
            return trace_id in self._records_by_trace_id

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[GovernanceTraceRecord]:
        self._validate_pagination(
            limit=limit,
            offset=offset,
        )

        with self._lock:
            records = self._sorted_records(
                self._records_by_trace_id.values()
            )

            return self._paginate(
                records,
                limit=limit,
                offset=offset,
            )

    def query(
        self,
        query: GovernanceTraceQuery,
    ) -> Sequence[GovernanceTraceRecord]:
        with self._lock:
            records = [
                record
                for record in self._records_by_trace_id.values()
                if self._matches_query(record, query)
            ]

            ordered_records = self._sorted_records(records)

            return self._paginate(
                ordered_records,
                limit=query.limit,
                offset=query.offset,
            )

    def count(
        self,
        query: GovernanceTraceQuery | None = None,
    ) -> int:
        with self._lock:
            if query is None:
                return len(self._records_by_trace_id)

            return sum(
                1
                for record in self._records_by_trace_id.values()
                if self._matches_query(record, query)
            )

    def statistics(self) -> GovernanceTraceRepositoryStatistics:
        with self._lock:
            records = tuple(self._records_by_trace_id.values())

            total_traces = len(records)

            completed_traces = sum(
                1
                for record in records
                if record.completed
            )

            active_traces = total_traces - completed_traces

            succeeded_traces = self._count_final_status(
                records,
                "succeeded",
            )
            failed_traces = self._count_final_status(
                records,
                "failed",
            )
            blocked_traces = self._count_final_status(
                records,
                "blocked",
            )
            rejected_traces = self._count_final_status(
                records,
                "rejected",
            )

            return GovernanceTraceRepositoryStatistics(
                total_traces=total_traces,
                completed_traces=completed_traces,
                active_traces=active_traces,
                succeeded_traces=succeeded_traces,
                failed_traces=failed_traces,
                blocked_traces=blocked_traces,
                rejected_traces=rejected_traces,
            )

    def delete(
        self,
        trace_id: str,
    ) -> bool:
        with self._lock:
            record = self._records_by_trace_id.pop(
                trace_id,
                None,
            )

            if record is None:
                return False

            self._trace_id_by_deployment_id.pop(
                record.deployment_id,
                None,
            )

            return True

    def save_many(
        self,
        records: Sequence[GovernanceTraceRecord],
    ) -> Sequence[GovernanceTraceRecord]:
        """
        Persist multiple records atomically from the perspective of this
        in-memory repository.

        All conflicts are validated before any record is written.
        """

        records = tuple(records)

        with self._lock:
            self._validate_batch(records)

            for record in records:
                self._store(record)

            return records

    def clear(self) -> None:
        """
        Remove all persisted records.

        This method is intentionally specific to the in-memory implementation
        and is useful for isolated tests and local development.
        """

        with self._lock:
            self._records_by_trace_id.clear()
            self._trace_id_by_deployment_id.clear()

    def _store(
        self,
        record: GovernanceTraceRecord,
    ) -> None:
        self._records_by_trace_id[record.trace_id] = record
        self._trace_id_by_deployment_id[
            record.deployment_id
        ] = record.trace_id

    def _ensure_trace_id_available(
        self,
        trace_id: str,
    ) -> None:
        if trace_id in self._records_by_trace_id:
            raise GovernanceTraceAlreadyExistsError(
                f"governance trace '{trace_id}' already exists"
            )

    def _ensure_deployment_id_available(
        self,
        deployment_id: str,
    ) -> None:
        existing_trace_id = self._trace_id_by_deployment_id.get(
            deployment_id
        )

        if existing_trace_id is not None:
            raise GovernanceTraceAlreadyExistsError(
                "deployment "
                f"'{deployment_id}' is already associated with "
                f"governance trace '{existing_trace_id}'"
            )

    def _validate_batch(
        self,
        records: Sequence[GovernanceTraceRecord],
    ) -> None:
        batch_trace_ids: set[str] = set()
        batch_deployment_ids: set[str] = set()

        for record in records:
            self._ensure_trace_id_available(record.trace_id)
            self._ensure_deployment_id_available(record.deployment_id)

            if record.trace_id in batch_trace_ids:
                raise GovernanceTraceAlreadyExistsError(
                    "duplicate governance trace identifier "
                    f"'{record.trace_id}' in batch"
                )

            if record.deployment_id in batch_deployment_ids:
                raise GovernanceTraceAlreadyExistsError(
                    "duplicate deployment identifier "
                    f"'{record.deployment_id}' in batch"
                )

            batch_trace_ids.add(record.trace_id)
            batch_deployment_ids.add(record.deployment_id)

    @staticmethod
    def _matches_query(
        record: GovernanceTraceRecord,
        query: GovernanceTraceQuery,
    ) -> bool:
        if (
            query.deployment_id is not None
            and record.deployment_id != query.deployment_id
        ):
            return False

        if (
            query.service_name is not None
            and record.service_name != query.service_name
        ):
            return False

        if (
            query.environment is not None
            and record.environment != query.environment
        ):
            return False

        if (
            query.governance_state is not None
            and record.governance_state != query.governance_state
        ):
            return False

        if (
            query.final_status is not None
            and record.final_status != query.final_status
        ):
            return False

        if (
            query.completed is not None
            and record.completed != query.completed
        ):
            return False

        if (
            query.created_after is not None
            and record.created_at < query.created_after
        ):
            return False

        if (
            query.created_before is not None
            and record.created_at > query.created_before
        ):
            return False

        return True

    @staticmethod
    def _sorted_records(
        records,
    ) -> tuple[GovernanceTraceRecord, ...]:
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    record.created_at,
                    record.trace_id,
                ),
                reverse=True,
            )
        )

    @staticmethod
    def _paginate(
        records: Sequence[GovernanceTraceRecord],
        *,
        limit: int | None,
        offset: int,
    ) -> tuple[GovernanceTraceRecord, ...]:
        if limit is None:
            return tuple(records[offset:])

        return tuple(
            records[offset : offset + limit]
        )

    @staticmethod
    def _validate_pagination(
        *,
        limit: int | None,
        offset: int,
    ) -> None:
        if limit is not None and limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        if offset < 0:
            raise ValueError(
                "offset cannot be negative"
            )

    @staticmethod
    def _count_final_status(
        records: Sequence[GovernanceTraceRecord],
        status: str,
    ) -> int:
        return sum(
            1
            for record in records
            if record.final_status == status
        )
