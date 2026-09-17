from dataclasses import dataclass


@dataclass
class CostAllocation:

    component: str

    monthly_cost_usd: float

    cost_percent: float


class CostAllocationEngine:

    def generate(
        self
    ):

        return [

            CostAllocation(

                component=
                    "compute",

                monthly_cost_usd=
                    25.0,

                cost_percent=
                    51.0
            ),

            CostAllocation(

                component=
                    "storage",

                monthly_cost_usd=
                    12.0,

                cost_percent=
                    24.5
            ),

            CostAllocation(

                component=
                    "network",

                monthly_cost_usd=
                    8.0,

                cost_percent=
                    16.3
            ),

            CostAllocation(

                component=
                    "monitoring",

                monthly_cost_usd=
                    4.0,

                cost_percent=
                    8.2
            )
        ]
