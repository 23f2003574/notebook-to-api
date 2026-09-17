from dataclasses import dataclass


@dataclass
class FailurePattern:

    pattern_name: str

    frequency: str

    severity: str


class FailurePatternDetectionEngine:

    def generate(
        self
    ):

        return [

            FailurePattern(

                pattern_name=
                    "high_latency",

                frequency=
                    "common",

                severity=
                    "medium"
            ),

            FailurePattern(

                pattern_name=
                    "dependency_timeout",

                frequency=
                    "occasional",

                severity=
                    "high"
            ),

            FailurePattern(

                pattern_name=
                    "resource_exhaustion",

                frequency=
                    "rare",

                severity=
                    "critical"
            )
        ]
