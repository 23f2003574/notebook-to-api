from dataclasses import dataclass


@dataclass
class PerformanceRecommendation:

    recommendation: str

    category: str

    priority: str


class PerformanceRecommendationEngine:

    def generate(
        self
    ):

        return [

            PerformanceRecommendation(

                recommendation=
                    "introduce_distributed_caching",

                category=
                    "latency",

                priority=
                    "high"
            ),

            PerformanceRecommendation(

                recommendation=
                    "implement_connection_pooling",

                category=
                    "database",

                priority=
                    "high"
            ),

            PerformanceRecommendation(

                recommendation=
                    "enable_async_processing",

                category=
                    "throughput",

                priority=
                    "medium"
            )
        ]
