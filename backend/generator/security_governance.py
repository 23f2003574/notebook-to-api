from dataclasses import dataclass


@dataclass
class SecurityGovernance:

    security_owner: str

    review_frequency: str

    compliance_review_required: bool

    incident_review_required: bool


class SecurityGovernanceEngine:

    def generate(
        self
    ):

        return SecurityGovernance(

            security_owner=
                "platform_team",

            review_frequency=
                "quarterly",

            compliance_review_required=
                True,

            incident_review_required=
                True
        )
