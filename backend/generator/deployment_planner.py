from dataclasses import dataclass


@dataclass
class DeploymentPlan:

    recommended_target: str

    rationale: list[str]

    fallback_targets: list[str]

    estimated_complexity: str


class DeploymentPlanner:

    def create_plan(
        self,
        recommendation,
        costs,
        validation_results
    ):

        validation_passed = all(
            result.passed
            for result
            in validation_results
        )

        primary = (
            recommendation
            .primary_target
        )

        complexity = "unknown"

        for cost in costs:

            if (
                cost.target
                == primary
            ):

                complexity = (
                    cost.complexity
                )

                break

        rationale = []

        if validation_passed:

            rationale.append(
                "Validation passed"
            )

        rationale.append(
            "Deployment target supported"
        )

        rationale.append(
            "Recommended by deployment engine"
        )

        return DeploymentPlan(
            recommended_target=
                primary,

            rationale=
                rationale,

            fallback_targets=
                recommendation
                .alternatives,

            estimated_complexity=
                complexity
        )