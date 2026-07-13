from dataclasses import dataclass
from typing import Dict, List, Optional


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

    def __init__(
        self,
        trace_engine
    ):

        self.trace_engine = (
            trace_engine
        )

        self._traces: Dict[
            str,
            object
        ] = {}

        self._deployment_index: Dict[
            str,
            str
        ] = {}

    def register(
        self,
        trace
    ):

        if trace.trace_id in self._traces:

            raise ValueError(
                "deployment governance trace "
                "is already registered"
            )

        if (
            trace.deployment_id
            in self._deployment_index
        ):

            raise ValueError(
                "a governance trace is already "
                "registered for this deployment"
            )

        self._traces[
            trace.trace_id
        ] = trace

        self._deployment_index[
            trace.deployment_id
        ] = trace.trace_id

        return trace

    def get_by_trace_id(
        self,
        trace_id: str
    ):

        return (
            self
            ._traces
            .get(
                trace_id
            )
        )

    def get_by_deployment_id(
        self,
        deployment_id: str
    ):

        trace_id = (
            self
            ._deployment_index
            .get(
                deployment_id
            )
        )

        if trace_id is None:

            return None

        return (
            self
            ._traces
            .get(
                trace_id
            )
        )

    def list_all(
        self
    ):

        return sorted(
            self
            ._traces
            .values(),
            key=lambda trace:
                trace.created_at,
            reverse=True
        )

    def query(
        self,
        query: DeploymentGovernanceTraceQuery
    ):

        results = []

        for trace in (
            self
            ._traces
            .values()
        ):

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

    def statistics(
        self
    ):

        total_traces = (
            len(
                self._traces
            )
        )

        completed_traces = 0

        succeeded_traces = 0

        failed_traces = 0

        blocked_traces = 0

        rejected_traces = 0

        for trace in (
            self
            ._traces
            .values()
        ):

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
