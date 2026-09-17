from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, TypeVar

from .deployment_governance_trace_persistence_mapper import (
    DeploymentGovernanceTracePersistenceMapper,
)
from .deployment_governance_trace_repository import (
    DeploymentGovernanceTraceRepository,
    GovernanceTraceQuery,
    GovernanceTraceRepositoryStatistics,
)
from .in_memory_deployment_governance_trace_repository import (
    GovernanceTraceAlreadyExistsError,
    GovernanceTraceNotFoundError,
    InMemoryDeploymentGovernanceTraceRepository,
)


TResult = TypeVar("TResult")


@dataclass
class DeploymentGovernanceTraceQuery:

    service_name: Optional[str] = None

    environment: Optional[str] = None

    current_stage: Optional[str] = None

    final_status: Optional[str] = None

    completed: Optional[bool] = None


@dataclass
class DeploymentGovernanceTraceRegistryStatistics:

    total_traces: int

    completed_traces: int

    active_traces: int

    succeeded_traces: int

    failed_traces: int

    blocked_traces: int

    rejected_traces: int


class DeploymentGovernanceTraceRegistry:
    """
    Domain-facing registry for deployment governance traces.

    Persistence is delegated to a DeploymentGovernanceTraceRepository via a
    DeploymentGovernanceTracePersistenceMapper. The registry no longer owns
    raw storage directly; it owns governance-oriented access semantics.

    query()/statistics()/list_all() intentionally preserve the previous
    dictionary-backed behavior, which filters and aggregates on each trace's
    *latest event* (via trace_engine.summarize()) rather than the mapper's
    derived semantic governance_state. Those are different concepts: latest
    activity vs. current semantic state. Callers that want the newer,
    storage-independent semantic-state querying should use
    query_by_criteria()/repository_statistics() instead.
    """

    def __init__(
        self,
        trace_engine,
        repository: DeploymentGovernanceTraceRepository | None = None,
        persistence_mapper: (
            DeploymentGovernanceTracePersistenceMapper | None
        ) = None,
    ) -> None:

        self.trace_engine = (
            trace_engine
        )

        self._repository = (
            repository
            or InMemoryDeploymentGovernanceTraceRepository()
        )

        self._persistence_mapper = (
            persistence_mapper
            or DeploymentGovernanceTracePersistenceMapper()
        )

    @property
    def repository(self) -> DeploymentGovernanceTraceRepository:
        """
        Return the repository backing this registry.
        """

        return self._repository

    def register(self, trace):
        """
        Register and persist a new deployment governance trace.

        Preserves the original registry's duplicate-detection semantics and
        error messages.
        """

        if self._repository.exists(trace.trace_id):

            raise ValueError(
                "deployment governance trace "
                "is already registered"
            )

        if (
            self._repository.get_by_deployment_id(
                trace.deployment_id
            )
            is not None
        ):

            raise ValueError(
                "a governance trace is already "
                "registered for this deployment"
            )

        record = self._persistence_mapper.to_record(trace)

        self._repository.save(record)

        return trace

    def update(self, trace):
        """
        Persist the latest representation of an existing governance trace.

        Callers must call this after mutating a trace's events (e.g. via
        trace_engine.record_event) for the mutation to survive future reads,
        since retrieval now reconstructs traces from persisted records rather
        than returning a shared in-memory object reference.
        """

        record = self._persistence_mapper.to_record(trace)

        self._repository.update(record)

        return trace

    def upsert(self, trace):
        """
        Create or replace a governance trace using explicit upsert semantics.
        """

        record = self._persistence_mapper.to_record(trace)

        self._repository.upsert(record)

        return trace

    def get(self, trace_id: str):
        """
        Retrieve a governance trace by trace identifier.
        """

        return self.get_by_trace_id(trace_id)

    def get_by_trace_id(self, trace_id: str):

        record = self._repository.get_by_trace_id(trace_id)

        if record is None:

            return None

        return self._persistence_mapper.from_record(record)

    def get_by_deployment_id(self, deployment_id: str):

        record = self._repository.get_by_deployment_id(
            deployment_id
        )

        if record is None:

            return None

        return self._persistence_mapper.from_record(record)

    def require(self, trace_id: str):
        """
        Retrieve a trace or raise a clear lookup error.
        """

        trace = self.get(trace_id)

        if trace is None:

            raise LookupError(
                f"deployment governance trace '{trace_id}' was not found"
            )

        return trace

    def require_by_deployment_id(self, deployment_id: str):
        """
        Retrieve a deployment's governance trace or raise a lookup error.
        """

        trace = self.get_by_deployment_id(deployment_id)

        if trace is None:

            raise LookupError(
                "deployment governance trace for deployment "
                f"'{deployment_id}' was not found"
            )

        return trace

    def exists(self, trace_id: str) -> bool:

        return self._repository.exists(trace_id)

    def list_all(self):
        """
        Return all registered traces, newest first.

        Preserves the previous registry's sort behavior (created_at desc).
        """

        records = self._repository.list()

        traces = self._persistence_mapper.from_records(records)

        return sorted(
            traces,
            key=lambda trace: trace.created_at,
            reverse=True,
        )

    def query(self, query: DeploymentGovernanceTraceQuery):
        """
        Query traces using the original latest-event-based criteria.
        """

        results = []

        records = self._repository.list()

        traces = self._persistence_mapper.from_records(records)

        for trace in traces:

            summary = (
                self
                .trace_engine
                .summarize(
                    trace
                )
            )

            if (
                query.service_name
                is not None
                and
                trace.service_name
                !=
                query.service_name
            ):

                continue

            if (
                query.environment
                is not None
                and
                trace.environment
                !=
                query.environment
                .strip()
                .lower()
            ):

                continue

            if (
                query.current_stage
                is not None
                and
                summary.current_stage
                !=
                query.current_stage
                .strip()
                .lower()
            ):

                continue

            if (
                query.final_status
                is not None
                and
                summary.final_status
                !=
                query.final_status
                .strip()
                .lower()
            ):

                continue

            if (
                query.completed
                is not None
                and
                summary.completed
                !=
                query.completed
            ):

                continue

            results.append(
                trace
            )

        return sorted(
            results,
            key=lambda trace:
                trace.created_at,
            reverse=True
        )

    def statistics(self):
        """
        Return aggregate statistics using the original latest-event-based
        derivation (via trace_engine.summarize()).
        """

        records = self._repository.list()

        traces = self._persistence_mapper.from_records(records)

        total_traces = len(traces)

        completed_traces = 0

        succeeded_traces = 0

        failed_traces = 0

        blocked_traces = 0

        rejected_traces = 0

        for trace in traces:

            summary = (
                self
                .trace_engine
                .summarize(
                    trace
                )
            )

            if summary.completed:

                completed_traces += 1

            if summary.final_status == "succeeded":

                succeeded_traces += 1

            elif summary.final_status == "failed":

                failed_traces += 1

            elif summary.final_status == "blocked":

                blocked_traces += 1

            elif summary.final_status == "rejected":

                rejected_traces += 1

        active_traces = (
            total_traces
            -
            completed_traces
        )

        return (
            DeploymentGovernanceTraceRegistryStatistics(

                total_traces=
                    total_traces,

                completed_traces=
                    completed_traces,

                active_traces=
                    active_traces,

                succeeded_traces=
                    succeeded_traces,

                failed_traces=
                    failed_traces,

                blocked_traces=
                    blocked_traces,

                rejected_traces=
                    rejected_traces
            )
        )

    def list_traces(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple:
        """
        Return persisted governance traces in repository-defined stable order.

        Unlike list_all(), this supports pagination and defers ordering to
        the repository.
        """

        records = self._repository.list(
            limit=limit,
            offset=offset,
        )

        return self._persistence_mapper.from_records(records)

    def query_by_criteria(self, query: GovernanceTraceQuery) -> tuple:
        """
        Query governance traces using storage-independent semantic criteria
        (governance_state, completed, date ranges, pagination, ...).

        This is distinct from query(), which preserves the original
        latest-event-based filtering semantics.
        """

        records = self._repository.query(query)

        return self._persistence_mapper.from_records(records)

    def count(self, query: GovernanceTraceQuery | None = None) -> int:
        """
        Count all traces or traces matching a semantic query.
        """

        return self._repository.count(query)

    def repository_statistics(self) -> GovernanceTraceRepositoryStatistics:
        """
        Return aggregate governance statistics derived from semantic
        governance_state/final_status, sourced directly from the repository.
        """

        return self._repository.statistics()

    def delete(self, trace_id: str) -> bool:
        """
        Delete a persisted governance trace.
        """

        return self._repository.delete(trace_id)

    def register_many(self, traces: Sequence) -> tuple:
        """
        Persist multiple governance traces using repository batch semantics.
        """

        traces = tuple(traces)

        records = self._persistence_mapper.to_records(traces)

        self._repository.save_many(records)

        return traces

    def mutate(
        self,
        trace_id: str,
        operation: Callable[..., TResult],
    ) -> TResult:
        """
        Load a trace, execute a domain mutation, and persist the resulting
        trace. Persistence occurs only after the operation completes
        successfully.
        """

        trace = self.require(trace_id)

        result = operation(trace)

        self.update(trace)

        return result

    def mutate_by_deployment_id(
        self,
        deployment_id: str,
        operation: Callable[..., TResult],
    ) -> TResult:
        """
        Load a deployment's canonical trace, execute a mutation, and persist
        it.
        """

        trace = self.require_by_deployment_id(deployment_id)

        result = operation(trace)

        self.update(trace)

        return result
