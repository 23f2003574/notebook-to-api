from dataclasses import dataclass


@dataclass
class DataIntelligenceGovernance:

    governance_owner: str

    review_frequency: str

    data_quality_policy_required: bool

    lineage_validation_required: bool


class DataIntelligenceGovernanceEngine:

    def generate(
        self
    ):

        return DataIntelligenceGovernance(

            governance_owner=
                "enterprise_data_office",

            review_frequency=
                "monthly",

            data_quality_policy_required=
                True,

            lineage_validation_required=
                True
        )
