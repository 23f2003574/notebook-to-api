from dataclasses import dataclass


@dataclass
class AlertPolicy:

    metric_name: str

    threshold: float

    severity: str


class AlertPolicyEngine:

    def generate(
        self
    ):

        return [

            AlertPolicy(

                metric_name=
                    "error_rate",

                threshold=
                    5.0,

                severity=
                    "critical"
            ),

            AlertPolicy(

                metric_name=
                    "request_latency",

                threshold=
                    1000.0,

                severity=
                    "warning"
            )
        ]
