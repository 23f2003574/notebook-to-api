from dataclasses import dataclass


@dataclass
class CostAutomation:

    workflow_name: str

    triggers: list[str]

    actions: list[str]


class CostAutomationEngine:

    def generate(
        self
    ):

        return CostAutomation(

            workflow_name=
                "cost_control",

            triggers=[

                "budget_threshold_exceeded",

                "cost_spike_detected",

                "resource_waste_detected"
            ],

            actions=[

                "notify_finops_team",

                "generate_cost_report",

                "create_optimization_ticket"
            ]
        )
