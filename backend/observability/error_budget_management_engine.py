from dataclasses import dataclass


@dataclass
class ErrorBudget:

    service_name: str

    slo_target: float

    total_events: int

    failed_events: int

    allowed_failures: float

    remaining_budget: float

    exhausted: bool


class ErrorBudgetManagementEngine:

    def calculate(
        self,
        service_name: str,
        slo_target: float,
        total_events: int,
        failed_events: int
    ):

        allowed_failures = (
            total_events
            * (1 - slo_target / 100)
        )

        remaining_budget = (
            allowed_failures
            - failed_events
        )

        return ErrorBudget(

            service_name=
                service_name,

            slo_target=
                slo_target,

            total_events=
                total_events,

            failed_events=
                failed_events,

            allowed_failures=
                allowed_failures,

            remaining_budget=
                remaining_budget,

            exhausted=
                remaining_budget <= 0
        )
