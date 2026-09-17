from dataclasses import dataclass


@dataclass
class DataIntelligenceControlCenter:

    data_quality_enabled: bool

    data_lineage_enabled: bool

    data_catalog_enabled: bool

    data_governance_enabled: bool

    data_platform_readiness_enabled: bool

    data_recommendations_enabled: bool

    data_scorecard_enabled: bool

    data_report_enabled: bool


class DataIntelligenceControlCenterGenerator:

    def generate(
        self
    ):

        return DataIntelligenceControlCenter(

            data_quality_enabled=
                True,

            data_lineage_enabled=
                True,

            data_catalog_enabled=
                True,

            data_governance_enabled=
                True,

            data_platform_readiness_enabled=
                True,

            data_recommendations_enabled=
                True,

            data_scorecard_enabled=
                True,

            data_report_enabled=
                True
        )
