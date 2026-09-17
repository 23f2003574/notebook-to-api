from dataclasses import dataclass


@dataclass
class ReliabilityGovernance:

    reliability_owner: str

    review_frequency: str

    slo_review_required: bool

    incident_review_required: bool


class ReliabilityGovernanceEngine:

    def generate(
        self
    ):

        return ReliabilityGovernance(

            reliability_owner=
                "platform_team",

            review_frequency=
                "monthly",

            slo_review_required=
                True,

            incident_review_required=
                True
        )