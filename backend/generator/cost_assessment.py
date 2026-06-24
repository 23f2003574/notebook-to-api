from dataclasses import dataclass


@dataclass
class CostAssessment:

    monthly_cost_usd: float

    annual_cost_usd: float

    cost_grade: str

    budget_friendly: bool


class CostAssessmentEngine:

    def generate(
        self
    ):

        return CostAssessment(

            monthly_cost_usd=
                49.0,

            annual_cost_usd=
                588.0,

            cost_grade=
                "A",

            budget_friendly=
                True
        )
