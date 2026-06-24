from dataclasses import dataclass


@dataclass
class CostRemediation:

    issue_type: str

    remediation_actions: list[str]

    priority: str


class CostRemediationEngine:

    def generate(
        self
    ):

        return CostRemediation(

            issue_type=
                "budget_overrun",

            remediation_actions=[

                "scale_down_unused_resources",

                "enable_auto_scaling",

                "optimize_storage_usage",

                "reduce_idle_capacity"
            ],

            priority=
                "high"
        )
