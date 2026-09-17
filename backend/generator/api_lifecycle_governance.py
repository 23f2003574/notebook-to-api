from dataclasses import dataclass


@dataclass
class APILifecycleGovernance:

    api_owner: str

    lifecycle_review_frequency: str

    semantic_versioning_required: bool

    deprecation_policy_required: bool


class APILifecycleGovernanceEngine:

    def generate(
        self
    ):

        return APILifecycleGovernance(

            api_owner=
                "api_platform_team",

            lifecycle_review_frequency=
                "quarterly",

            semantic_versioning_required=
                True,

            deprecation_policy_required=
                True
        )
