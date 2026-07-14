from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class GovernanceTraceQuery:
    """
    Storage-independent query specification for deployment governance traces.

    The repository contract accepts semantic query criteria rather than
    exposing database-specific concepts such as SQL WHERE clauses.
    """

    deployment_id: str | None = None
    service_name: str | None = None
    environment: str | None = None
    governance_state: str | None = None
    final_status: str | None = None
    completed: bool | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be greater than zero")

        if self.offset < 0:
            raise ValueError("offset cannot be negative")

        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after > self.created_before
        ):
            raise ValueError(
                "created_after cannot be later than created_before"
            )


@dataclass(frozen=True)
class GovernanceTraceRecord:
    """
    Storage-neutral representation of a persisted governance trace.

    The repository stores serialized trace state rather than depending on
    one concrete in-memory trace class. This keeps persistence infrastructure
    isolated from domain implementation details.
    """

    trace_id: str
    deployment_id: str
    service_name: str
    environment: str
    artifact_digest: str
    created_at: datetime
    updated_at: datetime
    governance_state: str
    final_status: str | None
    completed: bool
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required_string_fields = {
            "trace_id": self.trace_id,
            "deployment_id": self.deployment_id,
            "service_name": self.service_name,
            "environment": self.environment,
            "artifact_digest": self.artifact_digest,
            "governance_state": self.governance_state,
        }

        for field_name, value in required_string_fields.items():
            if not value or not value.strip():
                raise ValueError(
                    f"{field_name} must be a non-empty string"
                )

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot be earlier than created_at"
            )


@dataclass(frozen=True)
class GovernanceTraceRepositoryStatistics:
    """
    Repository-level aggregate counts.

    These statistics intentionally mirror stable governance concepts rather
    than storage-engine implementation details.
    """

    total_traces: int
    completed_traces: int
    active_traces: int
    succeeded_traces: int
    failed_traces: int
    blocked_traces: int
    rejected_traces: int

    def __post_init__(self) -> None:
        values = (
            self.total_traces,
            self.completed_traces,
            self.active_traces,
            self.succeeded_traces,
            self.failed_traces,
            self.blocked_traces,
            self.rejected_traces,
        )

        if any(value < 0 for value in values):
            raise ValueError(
                "governance trace statistics cannot contain negative values"
            )


class DeploymentGovernanceTraceRepository(ABC):
    """
    Abstract persistence boundary for deployment governance traces.

    Domain and application services should depend on this contract rather
    than directly depending on dictionaries, SQLite, PostgreSQL, or another
    concrete storage technology.
    """

    @abstractmethod
    def save(self, record: GovernanceTraceRecord) -> GovernanceTraceRecord:
        """
        Persist a new governance trace.

        Implementations should reject duplicate trace identifiers unless
        their documented semantics explicitly support idempotent creation.
        """
        raise NotImplementedError

    @abstractmethod
    def update(self, record: GovernanceTraceRecord) -> GovernanceTraceRecord:
        """
        Replace the persisted representation of an existing trace.
        """
        raise NotImplementedError

    @abstractmethod
    def upsert(self, record: GovernanceTraceRecord) -> GovernanceTraceRecord:
        """
        Create or replace a trace using one storage-independent operation.
        """
        raise NotImplementedError

    @abstractmethod
    def get_by_trace_id(
        self,
        trace_id: str,
    ) -> GovernanceTraceRecord | None:
        """
        Retrieve a trace by its globally unique trace identifier.
        """
        raise NotImplementedError

    @abstractmethod
    def get_by_deployment_id(
        self,
        deployment_id: str,
    ) -> GovernanceTraceRecord | None:
        """
        Retrieve the trace associated with a deployment identifier.
        """
        raise NotImplementedError

    @abstractmethod
    def exists(
        self,
        trace_id: str,
    ) -> bool:
        """
        Return whether a trace exists without requiring callers to load it.
        """
        raise NotImplementedError

    @abstractmethod
    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[GovernanceTraceRecord]:
        """
        Return persisted governance traces in repository-defined stable order.
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        query: GovernanceTraceQuery,
    ) -> Sequence[GovernanceTraceRecord]:
        """
        Retrieve traces matching storage-independent governance criteria.
        """
        raise NotImplementedError

    @abstractmethod
    def count(
        self,
        query: GovernanceTraceQuery | None = None,
    ) -> int:
        """
        Count all traces or only traces matching a query.
        """
        raise NotImplementedError

    @abstractmethod
    def statistics(self) -> GovernanceTraceRepositoryStatistics:
        """
        Calculate aggregate governance trace statistics.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        trace_id: str,
    ) -> bool:
        """
        Delete a trace.

        Returns True when a persisted trace was removed and False when the
        identifier did not exist.
        """
        raise NotImplementedError

    def save_many(
        self,
        records: Iterable[GovernanceTraceRecord],
    ) -> Sequence[GovernanceTraceRecord]:
        """
        Default batch persistence operation.

        Concrete repositories may override this method with a transactional
        or otherwise optimized implementation.
        """

        return tuple(self.save(record) for record in records)
