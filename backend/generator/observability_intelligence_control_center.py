from dataclasses import dataclass


@dataclass
class ObservabilityIntelligenceControlCenter:

    health_checks_enabled: bool

    metrics_enabled: bool

    logging_enabled: bool

    alerting_enabled: bool

    dashboards_enabled: bool

    tracing_enabled: bool

    dependencies_enabled: bool

    incident_analysis_enabled: bool

    slo_enabled: bool

    observability_report_enabled: bool


class ObservabilityIntelligenceControlCenterGenerator:

    def generate(
        self
    ):

        return (
            ObservabilityIntelligenceControlCenter(

                health_checks_enabled=
                    True,

                metrics_enabled=
                    True,

                logging_enabled=
                    True,

                alerting_enabled=
                    True,

                dashboards_enabled=
                    True,

                tracing_enabled=
                    True,

                dependencies_enabled=
                    True,

                incident_analysis_enabled=
                    True,

                slo_enabled=
                    True,

                observability_report_enabled=
                    True
            )
        )
