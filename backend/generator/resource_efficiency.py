from dataclasses import dataclass


@dataclass
class ResourceEfficiency:

    cpu_utilization_percent: float

    memory_utilization_percent: float

    storage_utilization_percent: float

    efficiency_score: float


class ResourceEfficiencyEngine:

    def generate(
        self
    ):

        return ResourceEfficiency(

            cpu_utilization_percent=
                78.0,

            memory_utilization_percent=
                72.0,

            storage_utilization_percent=
                69.0,

            efficiency_score=
                88.0
        )
