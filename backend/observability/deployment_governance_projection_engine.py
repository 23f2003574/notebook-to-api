from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DeploymentGovernanceListProjection:

    trace_id: str

    deployment_id: str

    service_name: str

    environment: str

    created_at: str

    current_stage: str

    current_stage_label: str

    final_status: str

    final_status_label: str

    completed: bool

    event_count: int


@dataclass
class DeploymentGovernanceTimelineEventProjection:

    sequence: int

    event_id: str

    event_type: str

    event_label: str

    status: str

    status_label: str

    reference_id: Optional[str]

    timestamp: str

    details: str


@dataclass
class DeploymentGovernanceDetailProjection:

    trace_id: str

    deployment_id: str

    service_name: str

    environment: str

    artifact_digest: str

    created_at: str

    current_stage: str

    current_stage_label: str

    final_status: str

    final_status_label: str

    completed: bool

    event_count: int

    timeline: List[
        DeploymentGovernanceTimelineEventProjection
    ]


class DeploymentGovernanceProjectionEngine:

    def __init__(
        self,
        trace_engine
    ):

        self.trace_engine = (
            trace_engine
        )

    def _humanize(
        self,
        value: str
    ):

        return (
            value
            .replace(
                "_",
                " "
            )
            .title()
        )

    def project_list_item(
        self,
        trace
    ):

        if trace is None:

            raise ValueError(
                "deployment governance trace "
                "must not be None"
            )

        summary = (
            self
            .trace_engine
            .summarize(
                trace
            )
        )

        return (
            DeploymentGovernanceListProjection(

                trace_id=
                    trace.trace_id,

                deployment_id=
                    trace.deployment_id,

                service_name=
                    trace.service_name,

                environment=
                    trace.environment,

                created_at=
                    trace.created_at,

                current_stage=
                    summary.current_stage,

                current_stage_label=
                    self
                    ._humanize(
                        summary.current_stage
                    ),

                final_status=
                    summary.final_status,

                final_status_label=
                    self
                    ._humanize(
                        summary.final_status
                    ),

                completed=
                    summary.completed,

                event_count=
                    summary.total_events
            )
        )

    def project_list(
        self,
        traces
    ):

        return [

            self
            .project_list_item(
                trace
            )

            for trace in traces
        ]

    def project_detail(
        self,
        trace
    ):

        if trace is None:

            raise ValueError(
                "deployment governance trace "
                "must not be None"
            )

        summary = (
            self
            .trace_engine
            .summarize(
                trace
            )
        )

        timeline = [

            DeploymentGovernanceTimelineEventProjection(

                sequence=
                    sequence,

                event_id=
                    event.event_id,

                event_type=
                    event.event_type,

                event_label=
                    self
                    ._humanize(
                        event.event_type
                    ),

                status=
                    event.status,

                status_label=
                    self
                    ._humanize(
                        event.status
                    ),

                reference_id=
                    event.reference_id,

                timestamp=
                    event.timestamp,

                details=
                    event.details
            )

            for sequence, event
            in enumerate(
                trace.events,
                start=1
            )
        ]

        return (
            DeploymentGovernanceDetailProjection(

                trace_id=
                    trace.trace_id,

                deployment_id=
                    trace.deployment_id,

                service_name=
                    trace.service_name,

                environment=
                    trace.environment,

                artifact_digest=
                    trace.artifact_digest,

                created_at=
                    trace.created_at,

                current_stage=
                    summary.current_stage,

                current_stage_label=
                    self
                    ._humanize(
                        summary.current_stage
                    ),

                final_status=
                    summary.final_status,

                final_status_label=
                    self
                    ._humanize(
                        summary.final_status
                    ),

                completed=
                    summary.completed,

                event_count=
                    summary.total_events,

                timeline=
                    timeline
            )
        )
