from dataclasses import dataclass


@dataclass
class PlatformMetric:

    name: str

    value: float

    unit: str


@dataclass
class PlatformHealth:

    status: str

    metrics: list[PlatformMetric]


class PlatformObservabilityEngine:

    def collect(self):

        return PlatformHealth(

            status="healthy",

            metrics=[

                PlatformMetric(

                    name="active_requests",

                    value=0,

                    unit="count"
                ),

                PlatformMetric(

                    name="cpu_usage",

                    value=0.0,

                    unit="percent"
                )
            ]
        )
