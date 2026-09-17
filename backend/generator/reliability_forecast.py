from dataclasses import dataclass


@dataclass
class ReliabilityForecast:

    projected_direction: str

    projected_risk: str

    confidence: str

    recommendation: str


class ReliabilityForecastEngine:

    def forecast(
        self,
        trend
    ):

        if (
            trend.direction
            == "degrading"
        ):

            return ReliabilityForecast(

                projected_direction=
                    "downward",

                projected_risk=
                    "high",

                confidence=
                    trend.confidence,

                recommendation=
                    (
                        "Address reliability "
                        "issues immediately"
                    )
            )

        if (
            trend.direction
            == "improving"
        ):

            return ReliabilityForecast(

                projected_direction=
                    "upward",

                projected_risk=
                    "low",

                confidence=
                    trend.confidence,

                recommendation=
                    (
                        "Continue current "
                        "reliability practices"
                    )
            )

        return ReliabilityForecast(

            projected_direction=
                "stable",

            projected_risk=
                "medium",

            confidence=
                trend.confidence,

            recommendation=
                (
                    "Continue monitoring "
                    "deployment reliability"
                )
        )