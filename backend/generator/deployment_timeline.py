from dataclasses import dataclass


@dataclass
class DeploymentEvent:

    timestamp: str

    event_type: str

    description: str


@dataclass
class DeploymentTimeline:

    events: list[DeploymentEvent]


class DeploymentTimelineGenerator:

    def generate(
        self,
        health,
        readiness,
        risk,
        incident
    ):

        events = [

            DeploymentEvent(
                timestamp=
                    "generated",

                event_type=
                    "health",

                description=
                    (
                        f"Health score "
                        f"{health.score}"
                    )
            ),

            DeploymentEvent(
                timestamp=
                    "generated",

                event_type=
                    "readiness",

                description=
                    (
                        f"Readiness score "
                        f"{readiness.score}"
                    )
            ),

            DeploymentEvent(
                timestamp=
                    "generated",

                event_type=
                    "risk",

                description=
                    (
                        f"Risk level "
                        f"{risk.level}"
                    )
            ),

            DeploymentEvent(
                timestamp=
                    "generated",

                event_type=
                    "incident",

                description=
                    incident.summary
            )
        ]

        return DeploymentTimeline(
            events=events
        )