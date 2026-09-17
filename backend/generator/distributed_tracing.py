from dataclasses import dataclass


@dataclass
class DistributedTracing:

    tracing_enabled: bool

    trace_provider: str

    span_collection_enabled: bool

    dependency_tracking_enabled: bool


class DistributedTracingEngine:

    def generate(
        self
    ):

        return DistributedTracing(

            tracing_enabled=
                True,

            trace_provider=
                "opentelemetry",

            span_collection_enabled=
                True,

            dependency_tracking_enabled=
                True
        )
