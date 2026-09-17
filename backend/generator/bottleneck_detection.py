from dataclasses import dataclass


@dataclass
class PerformanceBottleneck:

    component: str

    bottleneck_type: str

    severity: str


class BottleneckDetectionEngine:

    def generate(
        self
    ):

        return [

            PerformanceBottleneck(

                component=
                    "database",

                bottleneck_type=
                    "high_query_latency",

                severity=
                    "high"
            ),

            PerformanceBottleneck(

                component=
                    "api_gateway",

                bottleneck_type=
                    "request_queueing",

                severity=
                    "medium"
            ),

            PerformanceBottleneck(

                component=
                    "cache",

                bottleneck_type=
                    "low_hit_ratio",

                severity=
                    "medium"
            )
        ]
