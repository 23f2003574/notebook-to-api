from dataclasses import dataclass


@dataclass
class PerformanceGovernance:

    performance_owner: str

    review_frequency: str

    sla_review_required: bool

    benchmark_review_required: bool


class PerformanceGovernanceEngine:

    def generate(
        self
    ):

        return PerformanceGovernance(

            performance_owner=
                "platform_team",

            review_frequency=
                "monthly",

            sla_review_required=
                True,

            benchmark_review_required=
                True
        )
