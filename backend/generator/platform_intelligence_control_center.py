from dataclasses import dataclass


@dataclass
class PlatformIntelligenceControlCenter:

    platform_readiness_enabled: bool

    developer_experience_enabled: bool

    internal_developer_platform_enabled: bool

    platform_engineering_architecture_enabled: bool

    platform_operations_enabled: bool

    platform_recommendations_enabled: bool

    platform_scorecard_enabled: bool

    platform_report_enabled: bool


class PlatformIntelligenceControlCenterGenerator:

    def generate(
        self
    ):

        return PlatformIntelligenceControlCenter(
            platform_readiness_enabled=
                True,
            developer_experience_enabled=
                True,
            internal_developer_platform_enabled=
                True,
            platform_engineering_architecture_enabled=
                True,
            platform_operations_enabled=
                True,
            platform_recommendations_enabled=
                True,
            platform_scorecard_enabled=
                True,
            platform_report_enabled=
                True
        )
