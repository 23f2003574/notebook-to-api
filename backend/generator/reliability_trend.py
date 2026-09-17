from dataclasses import dataclass


@dataclass
class ReliabilityTrend:

    direction: str

    score: int

    confidence: str

    summary: str


class ReliabilityTrendAnalyzer:

    def analyze(
        self,
        patterns,
        metrics
    ):

        if not patterns:

            return ReliabilityTrend(
                direction="improving",

                score=
                    metrics.reliability_score,

                confidence="high",

                summary=
                    "No significant failure patterns detected"
            )

        high_severity_patterns = len(

            [
                pattern

                for pattern
                in patterns

                if pattern.severity
                == "high"
            ]
        )

        if high_severity_patterns > 0:

            return ReliabilityTrend(
                direction="degrading",

                score=
                    metrics.reliability_score,

                confidence="medium",

                summary=
                    (
                        "Critical failure patterns "
                        "require attention"
                    )
            )

        return ReliabilityTrend(
            direction="stable",

            score=
                metrics.reliability_score,

            confidence="medium",

            summary=
                "Reliability stable"
        )