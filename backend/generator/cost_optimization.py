from dataclasses import dataclass


@dataclass
class CostOptimization:

    recommendation: str

    estimated_monthly_savings_usd: float

    impact: str

    priority: str


class CostOptimizationEngine:

    def generate(
        self
    ):

        return [

            CostOptimization(

                recommendation=
                    "enable_auto_scaling",

                estimated_monthly_savings_usd=
                    15.0,

                impact=
                    "high",

                priority=
                    "high"
            ),

            CostOptimization(

                recommendation=
                    "reduce_idle_instances",

                estimated_monthly_savings_usd=
                    10.0,

                impact=
                    "medium",

                priority=
                    "medium"
            ),

            CostOptimization(

                recommendation=
                    "optimize_storage_tier",

                estimated_monthly_savings_usd=
                    8.0,

                impact=
                    "medium",

                priority=
                    "medium"
            )
        ]
