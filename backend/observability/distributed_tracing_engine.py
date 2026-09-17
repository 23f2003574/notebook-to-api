from dataclasses import dataclass


@dataclass
class TraceSpan:

    trace_id: str

    span_id: str

    operation_name: str

    component: str

    status: str


class DistributedTracingEngine:

    def start_span(
        self,
        trace_id: str,
        operation_name: str,
        component: str
    ):

        return TraceSpan(

            trace_id=
                trace_id,

            span_id=
                "span-001",

            operation_name=
                operation_name,

            component=
                component,

            status=
                "started"
        )
