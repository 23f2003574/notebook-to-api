from dataclasses import dataclass


@dataclass
class MetricDefinition:

    name: str

    metric_type: str

    description: str


class MetricsDefinitionEngine:

    def generate(
        self
    ):

        return [

            MetricDefinition(

                name=
                    "request_count",

                metric_type=
                    "counter",

                description=
                    "Total API requests"
            ),

            MetricDefinition(

                name=
                    "request_latency",

                metric_type=
                    "histogram",

                description=
                    "API response latency"
            ),

            MetricDefinition(

                name=
                    "error_count",

                metric_type=
                    "counter",

                description=
                    "Total API errors"
            )
        ]
