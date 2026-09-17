from dataclasses import dataclass


@dataclass
class CostScorecard:

    overall_score: float

    cost_grade: str

    monthly_cost_usd: float

    budget_utilization_percent: float

    risk_level: str

    optimization_count: int


class CostScorecardEngine:

    def generate(
        self
    ):

        return CostScorecard(

            overall_score=
                91.0,

            cost_grade=
                "A",

            monthly_cost_usd=
                49.0,

            budget_utilization_percent=
                65.3,

            risk_level=
                "low",

            optimization_count=
                3
        )
