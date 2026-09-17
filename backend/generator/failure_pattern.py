from dataclasses import dataclass


@dataclass
class FailurePattern:

    pattern_type: str

    occurrence_count: int

    severity: str

    description: str


class FailurePatternDetector:

    def detect(
        self,
        incident,
        analysis
    ):

        patterns = []

        if (
            incident.severity
            == "critical"
        ):

            patterns.append(

                FailurePattern(

                    pattern_type=
                        "deployment-readiness",

                    occurrence_count=1,

                    severity="high",

                    description=
                        analysis.root_cause
                )
            )

        elif (
            incident.severity
            == "warning"
        ):

            patterns.append(

                FailurePattern(

                    pattern_type=
                        "deployment-review",

                    occurrence_count=1,

                    severity="medium",

                    description=
                        analysis.root_cause
                )
            )

        return patterns