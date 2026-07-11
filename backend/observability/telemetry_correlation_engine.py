from dataclasses import dataclass


@dataclass
class TelemetryCorrelation:

    correlation_id: str

    trace_id: str

    metric_name: str

    log_component: str


class TelemetryCorrelationEngine:

    def correlate(
        self,
        trace_id: str,
        metric_name: str,
        log_component: str
    ):

        return TelemetryCorrelation(

            correlation_id=
                "correlation-001",

            trace_id=
                trace_id,

            metric_name=
                metric_name,

            log_component=
                log_component
        )
