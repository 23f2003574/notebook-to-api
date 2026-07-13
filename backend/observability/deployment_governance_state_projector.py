from dataclasses import dataclass
from typing import Optional


@dataclass
class DeploymentGovernanceState:

    deployment_id: str

    state: str

    terminal: bool

    actionable: bool

    blocking_reason: Optional[str]

    latest_event_type: Optional[str]

    latest_event_status: Optional[str]


class DeploymentGovernanceStateProjector:

    TERMINAL_STATES = {
        "succeeded",
        "failed",
        "blocked",
        "rejected"
    }

    def project(
        self,
        trace
    ):

        if not trace.events:

            return DeploymentGovernanceState(

                deployment_id=
                    trace.deployment_id,

                state=
                    "created",

                terminal=
                    False,

                actionable=
                    True,

                blocking_reason=
                    None,

                latest_event_type=
                    None,

                latest_event_status=
                    None
            )

        latest_event = (
            trace.events[-1]
        )

        state = (
            self
            ._derive_state(
                trace
            )
        )

        terminal = (
            state
            in
            self.TERMINAL_STATES
        )

        actionable = (
            state
            in {
                "created",
                "awaiting_approval",
                "approved",
                "ready_for_execution",
                "authorized_for_execution"
            }
        )

        blocking_reason = (
            self
            ._derive_blocking_reason(
                trace,
                state
            )
        )

        return DeploymentGovernanceState(

            deployment_id=
                trace.deployment_id,

            state=
                state,

            terminal=
                terminal,

            actionable=
                actionable,

            blocking_reason=
                blocking_reason,

            latest_event_type=
                latest_event.event_type,

            latest_event_status=
                latest_event.status
        )

    def _derive_state(
        self,
        trace
    ):

        events = (
            trace.events
        )

        latest = (
            events[-1]
        )

        if (
            latest.status
            ==
            "succeeded"
        ):

            return "succeeded"

        if (
            latest.status
            ==
            "failed"
        ):

            return "failed"

        if (
            latest.status
            ==
            "blocked"
        ):

            return "blocked"

        if (
            latest.status
            ==
            "rejected"
        ):

            return "rejected"

        if (
            latest.event_type
            ==
            "deployment_execution"
            and
            latest.status
            ==
            "started"
        ):

            return "executing"

        if (
            latest.event_type
            ==
            "execution_authorization"
            and
            latest.status
            ==
            "consumed"
        ):

            return "execution_handoff"

        if (
            latest.event_type
            ==
            "execution_authorization"
            and
            latest.status
            ==
            "issued"
        ):

            return "authorized_for_execution"

        if (
            latest.event_type
            ==
            "execution_eligibility"
        ):

            if (
                latest.status
                ==
                "execute"
            ):

                return "ready_for_execution"

            return "blocked"

        approval_decision = (
            self
            ._latest_event(
                events,
                "approval_decision"
            )
        )

        if approval_decision is not None:

            if (
                approval_decision.status
                ==
                "approved"
            ):

                return "approved"

            if (
                approval_decision.status
                ==
                "rejected"
            ):

                return "rejected"

        approval_request = (
            self
            ._latest_event(
                events,
                "approval_request"
            )
        )

        if (
            approval_request
            is not None
            and
            approval_request.status
            ==
            "pending"
        ):

            return "awaiting_approval"

        policy_event = (
            self
            ._latest_event(
                events,
                "policy_evaluation"
            )
        )

        if policy_event is not None:

            if (
                policy_event.status
                ==
                "allow"
            ):

                return "policy_allowed"

            if (
                policy_event.status
                ==
                "require_approval"
            ):

                return "awaiting_approval"

            if (
                policy_event.status
                in {
                    "block",
                    "blocked"
                }
            ):

                return "blocked"

        return "in_progress"

    def _latest_event(
        self,
        events,
        event_type: str
    ):

        for event in reversed(
            events
        ):

            if (
                event.event_type
                ==
                event_type
            ):

                return event

        return None

    def _derive_blocking_reason(
        self,
        trace,
        state: str
    ):

        if (
            state
            not in {
                "blocked",
                "failed",
                "rejected"
            }
        ):

            return None

        latest_event = (
            trace.events[-1]
        )

        return (
            latest_event.details
            or
            latest_event.status
        )
