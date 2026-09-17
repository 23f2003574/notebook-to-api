from dataclasses import dataclass


@dataclass
class CostIntelligenceControlCenter:

    cost_assessment_enabled: bool

    cost_forecasting_enabled: bool

    cost_optimization_enabled: bool

    resource_efficiency_enabled: bool

    cost_allocation_enabled: bool

    budget_planning_enabled: bool

    cost_risk_analysis_enabled: bool

    cost_scorecard_enabled: bool

    cost_report_enabled: bool


class CostIntelligenceControlCenterGenerator:

    def generate(
        self
    ):

        return (

            CostIntelligenceControlCenter(

                cost_assessment_enabled=
                    True,

                cost_forecasting_enabled=
                    True,

                cost_optimization_enabled=
                    True,

                resource_efficiency_enabled=
                    True,

                cost_allocation_enabled=
                    True,

                budget_planning_enabled=
                    True,

                cost_risk_analysis_enabled=
                    True,

                cost_scorecard_enabled=
                    True,

                cost_report_enabled=
                    True
            )
        )
