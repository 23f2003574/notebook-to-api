from dataclasses import dataclass


@dataclass
class PerformanceOptimization:

    recommendation: str

    expected_latency_reduction_ms: float

    expected_throughput_gain_percent: float

    priority: str


class PerformanceOptimizationEngine:

    def generate(
        self
    ):

        return [

            PerformanceOptimization(

                recommendation=
                    "optimize_database_indexes",

                expected_latency_reduction_ms=
                    12.5,

                expected_throughput_gain_percent=
                    18.0,

                priority=
                    "high"
            ),

            PerformanceOptimization(

                recommendation=
                    "enable_response_caching",

                expected_latency_reduction_ms=
                    8.0,

                expected_throughput_gain_percent=
                    12.0,

                priority=
                    "medium"
            ),

            PerformanceOptimization(

                recommendation=
                    "parallelize_io_operations",

                expected_latency_reduction_ms=
                    6.0,

                expected_throughput_gain_percent=
                    10.0,

                priority=
                    "medium"
            )
        ]
