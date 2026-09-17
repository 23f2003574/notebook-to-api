from dataclasses import dataclass


@dataclass
class PostIncidentAnalysis:

    incident_summary: str

    root_cause: str

    lessons_learned: list[str]

    prevention_actions: list[str]


class PostIncidentAnalyzer:

    def analyze(
        self,
        incident,
        recovery
    ):

        if (
            incident.severity
            == "critical"
        ):

            return (
                PostIncidentAnalysis(

                    incident_summary=
                        incident.summary,

                    root_cause=
                        "Deployment readiness failure",

                    lessons_learned=[
                        "Validate deployment before release",
                        "Review deployment health metrics"
                    ],

                    prevention_actions=[
                        "Strengthen validation checks",
                        "Improve deployment monitoring"
                    ]
                )
            )

        return (
            PostIncidentAnalysis(

                incident_summary=
                    incident.summary,

                root_cause=
                    "No significant issue detected",

                lessons_learned=[
                    "Current process effective"
                ],

                prevention_actions=[]
            )
        )