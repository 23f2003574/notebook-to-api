from dataclasses import dataclass


@dataclass
class IncidentAnalysis:

    incident_type: str

    probable_cause: str

    affected_component: str

    severity: str


class IncidentAnalysisEngine:

    def generate(
        self
    ):

        return IncidentAnalysis(

            incident_type=
                "service_degradation",

            probable_cause=
                "high_latency",

            affected_component=
                "api",

            severity=
                "high"
        )
