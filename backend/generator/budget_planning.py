from dataclasses import dataclass


@dataclass
class BudgetPlan:

    monthly_budget_usd: float

    annual_budget_usd: float

    budget_utilization_percent: float

    within_budget: bool


class BudgetPlanningEngine:

    def generate(
        self
    ):

        return BudgetPlan(

            monthly_budget_usd=
                75.0,

            annual_budget_usd=
                900.0,

            budget_utilization_percent=
                65.3,

            within_budget=
                True
        )
