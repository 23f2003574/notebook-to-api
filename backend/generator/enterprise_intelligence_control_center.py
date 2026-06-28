from dataclasses import dataclass


@dataclass
class EnterpriseIntelligenceControlCenter:

    enterprise_readiness_enabled: bool

    business_capability_mapping_enabled: bool

    enterprise_architecture_enabled: bool

    digital_transformation_enabled: bool

    enterprise_integration_enabled: bool

    enterprise_recommendations_enabled: bool

    enterprise_scorecard_enabled: bool

    enterprise_report_enabled: bool


class EnterpriseIntelligenceControlCenterGenerator:

    def generate(
        self
    ):

        return (
            EnterpriseIntelligenceControlCenter(
                enterprise_readiness_enabled=
                    True,
                business_capability_mapping_enabled=
                    True,
                enterprise_architecture_enabled=
                    True,
                digital_transformation_enabled=
                    True,
                enterprise_integration_enabled=
                    True,
                enterprise_recommendations_enabled=
                    True,
                enterprise_scorecard_enabled=
                    True,
                enterprise_report_enabled=
                    True
            )
        )
