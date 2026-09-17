from dataclasses import dataclass


@dataclass
class CapacityPlan:

    expected_peak_rps: float

    recommended_instances: int

    cpu_utilization_target: float

    scaling_strategy: str


class CapacityPlanningEngine:

    def generate(
        self
    ):

        return CapacityPlan(

            expected_peak_rps=
                4500.0,

            recommended_instances=
                6,

            cpu_utilization_target=
                70.0,

            scaling_strategy=
                "horizontal_auto_scaling"
        )
