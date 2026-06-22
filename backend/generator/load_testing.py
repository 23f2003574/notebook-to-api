from dataclasses import dataclass


@dataclass
class LoadTestPlan:

    concurrent_users: int

    requests_per_second: int

    duration_seconds: int

    target_latency_ms: int


class LoadTestingEngine:

    def generate(
        self
    ):

        return LoadTestPlan(

            concurrent_users=
                100,

            requests_per_second=
                500,

            duration_seconds=
                300,

            target_latency_ms=
                500
        )
