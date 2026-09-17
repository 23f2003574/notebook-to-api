from dataclasses import dataclass


@dataclass
class CostGovernance:

    budget_owner: str

    review_frequency: str

    budget_approval_required: bool

    cost_review_required: bool


class CostGovernanceEngine:

    def generate(
        self
    ):

        return CostGovernance(

            budget_owner=
                "platform_team",

            review_frequency=
                "monthly",

            budget_approval_required=
                True,

            cost_review_required=
                True
        )
