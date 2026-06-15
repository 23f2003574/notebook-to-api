from dataclasses import dataclass


@dataclass
class ReliabilityScorecard:

    score: int

    grade: str

    trend: str

    forecast_risk: str

    summary: str


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

            score=score,

            grade=grade,

            trend=
                trend.direction,

            forecast_risk=
                forecast.projected_risk,

            summary=
                (
                    f"Reliability grade "
                    f"{grade}"
                )
        )