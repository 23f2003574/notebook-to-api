from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4


@dataclass
class DeploymentGovernanceTraceEvent:

    event_id: str

    event_type: str

    status: str

    reference_id: Optional[str]

    timestamp: str

    details: str


@dataclass
class DeploymentGovernanceTrace:

    trace_id: str

    deployment_id: str

    service_name: str

    environment: str

    artifact_digest: str

    created_at: str

    events: List[
        DeploymentGovernanceTraceEvent
    ] = field(
        default_factory=list
    )


@dataclass
class DeploymentGovernanceTraceSummary:

    trace_id: str

    deployment_id: str

    current_stage: str

    final_status: str

    total_events: int

    completed: bool


class DeploymentGovernanceTraceEngine:

    def create(
        self,
        deployment_id: str,
        service_name: str,
        environment: str,
        artifact_digest: str
    ):

        return DeploymentGovernanceTrace(

            trace_id=
                str(uuid4()),

            deployment_id=
                deployment_id,

            service_name=
                service_name,

            environment=
                environment
                .strip()
                .lower(),

            artifact_digest=
                artifact_digest,

            created_at=
                datetime
                .now(timezone.utc)
                .isoformat()
        )

    def record_event(
        self,
        trace: DeploymentGovernanceTrace,
        event_type: str,
        status: str,
        details: str,
        reference_id: Optional[str] = None
    ):

        normalized_event_type = (
            event_type
            .strip()
            .lower()
        )

        normalized_status = (
            status
            .strip()
            .lower()
        )

        if not normalized_event_type:

            raise ValueError(
                "governance trace event type "
                "must not be empty"
            )

        if not normalized_status:

            raise ValueError(
                "governance trace event status "
                "must not be empty"
            )

        event = DeploymentGovernanceTraceEvent(

            event_id=
                str(uuid4()),

            event_type=
                normalized_event_type,

            status=
                normalized_status,

            reference_id=
                reference_id,

            timestamp=
                datetime
                .now(timezone.utc)
                .isoformat(),

            details=
                details
        )

        trace.events.append(
            event
        )

        return event

    def summarize(
        self,
        trace: DeploymentGovernanceTrace
    ):

        if not trace.events:

            return DeploymentGovernanceTraceSummary(

                trace_id=
                    trace.trace_id,

                deployment_id=
                    trace.deployment_id,

                current_stage=
                    "created",

                final_status=
                    "pending",

                total_events=
                    0,

                completed=
                    False
            )

        latest_event = (
            trace.events[-1]
        )

        terminal_statuses = {
            "succeeded",
            "failed",
            "blocked",
            "rejected"
        }

        completed = (
            latest_event.status
            in terminal_statuses
        )

        return DeploymentGovernanceTraceSummary(

            trace_id=
                trace.trace_id,

            deployment_id=
                trace.deployment_id,

            current_stage=
                latest_event.event_type,

            final_status=
                latest_event.status,

            total_events=
                len(trace.events),

            completed=
                completed
        )

    def validate_context(
        self,
        trace: DeploymentGovernanceTrace,
        deployment_id: str,
        artifact_digest: str,
        environment: str
    ):

        return (
            trace.deployment_id
            ==
            deployment_id

            and

            trace.artifact_digest
            ==
            artifact_digest

            and

            trace.environment
            ==
            environment
            .strip()
            .lower()
        )
