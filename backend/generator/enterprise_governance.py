from dataclasses import dataclass


@dataclass
class EnterpriseGovernance:

    governance_owner: str

    architecture_review_frequency: str

    architecture_board_required: bool

    transformation_review_required: bool


class EnterpriseGovernanceEngine:

    def generate(
        self
    ):

        return EnterpriseGovernance(
            governance_owner=
                "enterprise_architecture_board",
            architecture_review_frequency=
                "quarterly",
            architecture_board_required=
                True,
            transformation_review_required=
                True
        )
