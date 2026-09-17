from dataclasses import dataclass


@dataclass
class DigitalTransformation:

    transformation_strategy: str

    modernization_approach: str

    migration_strategy: str

    transformation_priority: str


class DigitalTransformationEngine:

    def generate(
        self
    ):

        return DigitalTransformation(
            transformation_strategy=
                "api_first_transformation",
            modernization_approach=
                "incremental_modernization",
            migration_strategy=
                "strangler_fig_pattern",
            transformation_priority=
                "high"
        )
