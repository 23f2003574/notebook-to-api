from dataclasses import dataclass


@dataclass
class MetricSample:

    metric_name: str

    value: float

    unit: str

    timestamp: str


class MetricsCollectionEngine:

    def collect(
        self,
        metric_name: str,
        value: float,
        unit: str
    ):

        return MetricSample(

            metric_name=
                metric_name,

            value=
                value,

            unit=
                unit,

            timestamp=
                "2026-07-11T00:00:00Z"
        )
