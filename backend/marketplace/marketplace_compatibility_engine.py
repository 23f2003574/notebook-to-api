from dataclasses import dataclass


@dataclass
class CompatibilityRequirement:

    notebook2api_version: str

    python_version: str

    required_capabilities: list[str]


@dataclass
class CompatibilityResult:

    compatible: bool

    missing_capabilities: list[str]

    message: str


class MarketplaceCompatibilityEngine:

    def validate(
        self,
        requirements: CompatibilityRequirement
    ):

        return CompatibilityResult(

            compatible=
                True,

            missing_capabilities=[],

            message=
                "Extension is compatible."
        )
