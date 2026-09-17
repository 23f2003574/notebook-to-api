from dataclasses import dataclass


@dataclass
class DeploymentReadiness:

    ready: bool

    score: int

    reasons: list[str]


class DeploymentReadinessAnalyzer:

    def analyze(
        self,
        compatibility,
        validation_results,
        plan
    ):

        reasons = []

        compatibility_ok = any(
            result.supported
            for result
            in compatibility
        )

        validation_ok = all(
            result.passed
            for result
            in validation_results
        )

        plan_ok = (
            plan.recommended_target
            is not None
        )

        if compatibility_ok:

            reasons.append(
                "Compatible deployment target found"
            )

        if validation_ok:

            reasons.append(
                "Validation checks passed"
            )

        if plan_ok:

            reasons.append(
                "Deployment plan generated"
            )

        score = sum([
            compatibility_ok,
            validation_ok,
            plan_ok
        ]) * 33

        return DeploymentReadiness(
            ready=
                compatibility_ok
                and
                validation_ok
                and
                plan_ok,

            score=score,

            reasons=reasons
        )