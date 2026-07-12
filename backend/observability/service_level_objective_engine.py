from dataclasses import dataclass


@dataclass
class ServiceLevelObjective:

    service_name: str

    indicator_name: str

    target: float

    current_value: float

    objective_met: bool


class ServiceLevelObjectiveEngine:

    def evaluate(
        self,
        service_name: str,
        indicator_name: str,
        target: float,
        current_value: float
    ):

        return ServiceLevelObjective(

            service_name=
                service_name,

            indicator_name=
                indicator_name,

            target=
                target,

            current_value=
                current_value,

            objective_met=
                current_value >= target
        )
