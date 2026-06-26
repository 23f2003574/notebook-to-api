from dataclasses import dataclass


@dataclass
class PerformanceAssessment:

    average_latency_ms: float

    throughput_rps: float

    performance_score: float

    performance_grade: str


class PerformanceAssessmentEngine:

    def generate(
        self
    ):

        return PerformanceAssessment(

            average_latency_ms=
                42.8,

            throughput_rps=
                850.0,

            performance_score=
                92.0,

            performance_grade=
                "A"
        )
