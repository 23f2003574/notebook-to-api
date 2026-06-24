from dataclasses import dataclass


@dataclass
class CostForecast:

    forecast_period_months: int

    projected_monthly_cost_usd: float

    projected_annual_cost_usd: float

    trend: str


class CostForecastingEngine:

    def generate(
        self
    ):

        return CostForecast(

            forecast_period_months=
                12,

            projected_monthly_cost_usd=
                65.0,

            projected_annual_cost_usd=
                780.0,

            trend=
                "increasing"
        )
