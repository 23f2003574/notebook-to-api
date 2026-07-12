from dataclasses import dataclass


@dataclass
class ErrorBudgetBurnRate:

    service_name: str

    budget_consumed_percentage: float

    window_elapsed_percentage: float

    burn_rate: float

    burning_too_fast: bool


class ErrorBudgetBurnRateEngine:

    def calculate(
        self,
        service_name: str,
        budget_consumed_percentage: float,
        window_elapsed_percentage: float
    ):

        burn_rate = (
            budget_consumed_percentage
            / window_elapsed_percentage
            if window_elapsed_percentage > 0
            else 0.0
        )

        return ErrorBudgetBurnRate(

            service_name=
                service_name,

            budget_consumed_percentage=
                budget_consumed_percentage,

            window_elapsed_percentage=
                window_elapsed_percentage,

            burn_rate=
                burn_rate,

            burning_too_fast=
                burn_rate > 1.0
        )
