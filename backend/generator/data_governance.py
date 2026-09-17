from dataclasses import dataclass


@dataclass
class DataGovernance:

    data_owner: str

    stewardship_model: str

    governance_policy: str

    compliance_required: bool


class DataGovernanceIntelligenceEngine:

    def generate(
        self
    ):

        return DataGovernance(

            data_owner=
                "data_platform_team",

            stewardship_model=
                "domain_driven",

            governance_policy=
                "enterprise_data_standard",

            compliance_required=
                True
        )
