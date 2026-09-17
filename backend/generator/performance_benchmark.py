from dataclasses import dataclass


@dataclass
class PerformanceBenchmark:

    target_latency_ms: int

    target_throughput_rps: int

    max_error_rate_percent: float

    benchmark_grade: str


class PerformanceBenchmarkEngine:

    def generate(
        self
    ):

        return PerformanceBenchmark(

            target_latency_ms=
                500,

            target_throughput_rps=
                1000,

            max_error_rate_percent=
                1.0,

            benchmark_grade=
                "production"
        )
