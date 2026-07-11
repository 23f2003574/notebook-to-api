from dataclasses import dataclass


@dataclass
class RootCauseAnalysis:

    incident_id: str

    suspected_component: str

    probable_cause: str

    confidence: float


class RootCauseAnalysisEngine:

    def analyze(
        self,
        incident_id: str,
        suspected_component: str,
        probable_cause: str
    ):

        return RootCauseAnalysis(

            incident_id=
                incident_id,

            suspected_component=
                suspected_component,

            probable_cause=
                probable_cause,

            confidence=
                1.0
        )
