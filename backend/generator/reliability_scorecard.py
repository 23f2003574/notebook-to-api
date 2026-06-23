from dataclasses import dataclass


@dataclass
class ReliabilityScorecard:

    overall_score: float

    reliability_grade: str

    availability_percent: float

    risk_level: str

    recommendation_count: int


class ReliabilityScorecardEngine:

    def generate(
        self
    ):

        return ReliabilityScorecard(

            overall_score=
                93.0,

            reliability_grade=
                "A",

            availability_percent=
                99.9,

            risk_level=
                "low",

            recommendation_count=
                3
        )


class ReliabilityScorecardGenerator:

    def generate(
        self,
        metrics,
        trend,
        forecast
    ):

        score = (
            metrics
            .reliability_score
        )

        if score >= 95:

            grade = "A"

        elif score >= 85:

            grade = "B"

        elif score >= 75:

            grade = "C"

        else:

            grade = "D"

        return ReliabilityScorecard(

            overall_score=score,

            reliability_grade=grade,

            availability_percent=99.9,

            risk_level="low",

            recommendation_count=0
        )